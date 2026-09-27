"""Idea 4: payments can't be processed twice.

The flow:
  1. The client has a held slot. We create a Razorpay ORDER for it.
  2. The browser opens Razorpay Checkout and the client pays.
  3. Razorpay tells OUR SERVER by sending a signed webhook.
     Only the webhook confirms the booking. The browser is never trusted.

Webhooks are delivered "at least once": Razorpay retries if we're slow or
down, so the same event can arrive several times, even at the same moment.
Three layers make that harmless:
  - WebhookEvent.event_id is UNIQUE: a repeated event can't be stored twice.
  - Recording the event and acting on it happen in ONE transaction, so if
    processing crashes, the record rolls back too and Razorpay's retry works.
  - Payment.status and confirm_booking() are themselves safe to repeat.
"""
import hashlib
import hmac

import requests
from django.conf import settings
from django.db import IntegrityError, transaction

from .booking import SlotTaken, confirm_booking
from .models import Booking, Payment, WebhookEvent

SESSION_PRICE_PAISE = 2_500_00  # ₹2,500. Money is stored as whole paise, never as floats.
RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


# ---------------------------------------------------------------- step 1

def create_razorpay_order(amount_paise: int, receipt: str) -> dict:
    """Ask Razorpay for a new order. Tests replace this function with a fake."""
    response = requests.post(
        RAZORPAY_ORDERS_URL,
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
        json={"amount": amount_paise, "currency": "INR", "receipt": receipt},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()  # contains "id", e.g. "order_Nx1a2b3c"


def start_payment(booking: Booking) -> Payment:
    """Return the booking's open Razorpay order, creating one if needed.

    Clicking "Pay" twice reuses the same order instead of creating a second one.
    """
    existing = booking.payments.filter(status=Payment.Status.CREATED).first()
    if existing:
        return existing
    order = create_razorpay_order(SESSION_PRICE_PAISE, receipt=f"booking-{booking.pk}")
    return Payment.objects.create(
        booking=booking, razorpay_order_id=order["id"], amount_paise=SESSION_PRICE_PAISE
    )


# ---------------------------------------------------------------- step 3

def signature_is_valid(raw_body: bytes, signature: str) -> bool:
    """Razorpay signs the exact request body with our shared webhook secret.

    If the signature matches, the request really came from Razorpay and wasn't
    changed on the way. compare_digest avoids leaking timing information.
    """
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        return False  # with no secret set, anyone could sign a fake event
    expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@transaction.atomic
def handle_webhook_event(event_id: str, payload: dict) -> str:
    """Process one webhook delivery. Returns what happened, for logging and tests."""
    try:
        with transaction.atomic():  # savepoint, so a duplicate doesn't break the outer transaction
            WebhookEvent.objects.create(event_id=event_id, event_type=payload.get("event", ""), payload=payload)
    except IntegrityError:
        return "duplicate"  # we've already processed this exact event

    if payload.get("event") != "payment.captured":
        return "ignored"  # recorded, but not an event we act on

    entity = payload["payload"]["payment"]["entity"]
    payment = Payment.objects.select_for_update().filter(razorpay_order_id=entity["order_id"]).first()
    if payment is None:
        return "unknown_order"
    if payment.status != Payment.Status.CREATED:
        return "already_handled"  # e.g. a different event about the same payment

    payment.razorpay_payment_id = entity["id"]
    if entity["amount"] != payment.amount_paise:
        payment.status = Payment.Status.NEEDS_REFUND  # never confirm a wrong amount
    else:
        try:
            confirm_booking(payment.booking_id)
            payment.status = Payment.Status.PAID
        except SlotTaken:
            # Paid after the hold ran out AND someone else took the slot.
            payment.status = Payment.Status.NEEDS_REFUND
    payment.save(update_fields=["razorpay_payment_id", "status"])
    return payment.status