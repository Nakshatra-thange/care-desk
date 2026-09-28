""" payments can't be processed twice.

Razorpay's API is never called in tests: create_razorpay_order is replaced
with a fake, and webhooks are signed with a test secret.
"""
import hashlib
import hmac
import json
import threading
from datetime import datetime, time, timedelta, timezone
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from core.booking import hold_slot
from core.models import AvailabilityRule, Booking, Client, Payment, Therapist, WebhookEvent

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
SLOT = datetime(2026, 10, 20, 13, 20, tzinfo=timezone.utc)
SECRET = "test-webhook-secret"
FAKE_ORDER = {"id": "order_TEST123"}


def setup_asha():
    asha = Therapist.objects.create(name="Asha", timezone="Asia/Kolkata", session_minutes=50)
    AvailabilityRule.objects.create(therapist=asha, weekday=1, start_time=time(18), end_time=time(21))
    return asha


def make_client(n):
    return Client.objects.create(name=f"Client {n}", email=f"client{n}@example.com")


def captured_event(order_id="order_TEST123", amount=250000):
    return {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_TEST456", "order_id": order_id, "amount": amount}}},
    }


class WebhookHelpers:
    def deliver(self, payload, event_id="evt_1", secret=SECRET):
        body = json.dumps(payload).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post(
            "/api/webhooks/razorpay/", data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )


@override_settings(RAZORPAY_WEBHOOK_SECRET=SECRET, RAZORPAY_KEY_ID="rzp_test_key")
@mock.patch("core.payments.create_razorpay_order", return_value=FAKE_ORDER)
@mock.patch("django.utils.timezone.now", return_value=NOW)
class PaymentFlowTests(WebhookHelpers, TestCase):
    def setUp(self):
        self.asha = setup_asha()
        self.priya = make_client(1)
        self.booking = hold_slot(self.priya, self.asha.pk, SLOT, now=NOW)

    def pay(self):
        return self.client.post(f"/api/bookings/{self.booking.pk}/pay/")

    def test_pay_creates_one_order_even_if_clicked_twice(self, _now, fake_order):
        first, second = self.pay(), self.pay()
        self.assertEqual(first.json()["order_id"], "order_TEST123")
        self.assertEqual(first.json()["amount"], 250000)
        self.assertEqual(second.json()["order_id"], "order_TEST123")
        self.assertEqual(fake_order.call_count, 1)  # Razorpay was asked only once

    def test_paying_does_not_confirm_by_itself(self, _now, _order):
        self.pay()
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.HELD)  # only the webhook confirms

    def test_webhook_confirms_the_booking(self, _now, _order):
        self.pay()
        res = self.deliver(captured_event())
        self.assertEqual(res.json()["result"], "paid")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(Payment.objects.get().razorpay_payment_id, "pay_TEST456")

    def test_the_same_event_three_times_is_processed_once(self, _now, _order):
        self.pay()
        results = [self.deliver(captured_event(), event_id="evt_same").json()["result"] for _ in range(3)]
        self.assertEqual(results, ["paid", "duplicate", "duplicate"])
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_a_second_event_about_the_same_payment_changes_nothing(self, _now, _order):
        self.pay()
        self.deliver(captured_event(), event_id="evt_1")
        res = self.deliver(captured_event(), event_id="evt_2")
        self.assertEqual(res.json()["result"], "already_handled")

    def test_forged_webhook_is_rejected(self, _now, _order):
        self.pay()
        res = self.deliver(captured_event(), secret="wrong-secret")
        self.assertEqual(res.status_code, 400)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.HELD)

    def test_webhooks_are_rejected_when_no_secret_is_configured(self, _now, _order):
        self.pay()
        with override_settings(RAZORPAY_WEBHOOK_SECRET=""):
            res = self.deliver(captured_event(), secret="")
        self.assertEqual(res.status_code, 400)

    def test_wrong_amount_is_never_confirmed(self, _now, _order):
        self.pay()
        res = self.deliver(captured_event(amount=100))
        self.assertEqual(res.json()["result"], "needs_refund")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.HELD)

    def test_late_payment_for_a_lost_slot_is_flagged_for_refund(self, _now, _order):
        self.pay()
        later = NOW + timedelta(minutes=20)
        karan_hold = hold_slot(make_client(2), self.asha.pk, SLOT, now=later)  # Priya's hold ran out
        with mock.patch("django.utils.timezone.now", return_value=later):
            res = self.deliver(captured_event())
        self.assertEqual(res.json()["result"], "needs_refund")
        karan_hold.refresh_from_db()
        self.assertEqual(karan_hold.status, Booking.Status.HELD)  # Karan keeps his slot

    def test_cannot_pay_for_an_expired_hold(self, _now, _order):
        with mock.patch("django.utils.timezone.now", return_value=NOW + timedelta(minutes=11)):
            res = self.pay()
        self.assertEqual(res.status_code, 409)

    def test_page_can_poll_the_booking_status(self, _now, _order):
        self.pay()
        self.deliver(captured_event())
        res = self.client.get(f"/api/bookings/{self.booking.pk}/")
        self.assertEqual(res.json()["status"], "confirmed")


@override_settings(RAZORPAY_WEBHOOK_SECRET=SECRET)
@mock.patch("django.utils.timezone.now", return_value=NOW)
class ConcurrentWebhookTests(WebhookHelpers, TransactionTestCase):
    def test_duplicate_deliveries_at_the_same_instant_are_processed_once(self, _now):
        asha = setup_asha()
        booking = hold_slot(make_client(1), asha.pk, SLOT, now=NOW)
        Payment.objects.create(booking=booking, razorpay_order_id="order_TEST123", amount_paise=250000)

        barrier = threading.Barrier(5)
        results, lock = [], threading.Lock()

        def worker():
            try:
                barrier.wait()
                outcome = self.deliver(captured_event(), event_id="evt_same").json()["result"]
            except Exception as e:
                outcome = f"error: {e!r}"
            finally:
                connection.close()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count("paid"), 1, results)
        self.assertEqual(results.count("duplicate"), 4, results)