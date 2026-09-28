"""API endpoints. @api_view is Django REST Framework's version of FastAPI's @app.get."""
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, available_timezones
from django.db import transaction
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .booking import SlotTaken, hold_slot , release_hold
from .models import Booking, Client, Therapist
from .payments import SESSION_PRICE_PAISE, handle_webhook_event, signature_is_valid, start_payment
from .scheduling import UTC, get_open_slots

MAX_DAYS = 31


@api_view(["GET"])
def therapist_list(request):
    therapists = Therapist.objects.order_by("name")
    return Response([
        {"id": t.id, "name": t.name, "timezone": t.timezone, "session_minutes": t.session_minutes}
        for t in therapists
    ])


@api_view(["GET"])
def therapist_slots(request, pk):
    """GET /api/therapists/<pk>/slots/?start_date=2026-10-20&end_date=2026-10-27&tz=Europe/London

    Dates are the CLIENT's calendar days in `tz`. Times come back in UTC
    plus a ready-to-show local version.
    """
    therapist = get_object_or_404(Therapist, pk=pk)

    tz_name = request.query_params.get("tz", "UTC")
    if tz_name not in available_timezones():
        return Response({"detail": f"Unknown timezone: {tz_name}"}, status=400)
    try:
        first = date.fromisoformat(request.query_params["start_date"])
        last = date.fromisoformat(request.query_params["end_date"])
    except (KeyError, ValueError):
        return Response({"detail": "start_date and end_date are required, as YYYY-MM-DD."}, status=400)
    if not (0 <= (last - first).days < MAX_DAYS):
        return Response({"detail": f"end_date must be on or after start_date, within {MAX_DAYS} days."}, status=400)

    # The client's local midnights, converted to UTC, define the search window.
    client_tz = ZoneInfo(tz_name)
    window_start = datetime.combine(first, time.min, tzinfo=client_tz).astimezone(UTC)
    window_end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=client_tz).astimezone(UTC)

    therapist_tz = ZoneInfo(therapist.timezone)
    slots = get_open_slots(therapist, window_start, window_end)

    return Response({
        "therapist": {"id": therapist.id, "name": therapist.name, "timezone": therapist.timezone},
        "tz": tz_name,
        "slots": [
            {
                "start": s.start.isoformat().replace("+00:00", "Z"),
                "end": s.end.isoformat().replace("+00:00", "Z"),
                "local_date": s.start.astimezone(client_tz).date().isoformat(),
                "local_time": s.start.astimezone(client_tz).strftime("%H:%M"),
                "therapist_time": s.start.astimezone(therapist_tz).strftime("%H:%M"),
            }
            for s in slots
        ],
    })


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def booking_json(b: Booking) -> dict:
    return {
        "id": b.id,
        "status": b.status,
        "start": iso(b.during.lower),
        "end": iso(b.during.upper),
        "hold_expires_at": iso(b.hold_expires_at),
        "price_paise": SESSION_PRICE_PAISE,
        "therapist": b.therapist.name,
        "client": b.client.name,
    }


@api_view(["GET"])
def client_list(request):
    """Demo only: lets the page choose which fake client is booking."""
    return Response([{"id": c.id, "name": c.name} for c in Client.objects.order_by("name")])


@api_view(["POST"])
def booking_hold(request):
    """POST /api/bookings/hold/  {"client_id": 1, "therapist_id": 1, "start": "2026-10-19T12:30:00Z"}"""
    client = get_object_or_404(Client, pk=request.data.get("client_id"))
    therapist = get_object_or_404(Therapist, pk=request.data.get("therapist_id"))
    try:
        start = datetime.fromisoformat(request.data["start"])
    except (KeyError, TypeError, ValueError):
        return Response({"detail": "start must be an ISO datetime, e.g. 2026-10-19T12:30:00Z"}, status=400)
    if start.tzinfo is None:
        return Response({"detail": "start must include a timezone, e.g. ...Z"}, status=400)

    try:
        booking = hold_slot(client, therapist.pk, start)
    except SlotTaken as e:
        return Response({"detail": str(e)}, status=409)
    return Response(booking_json(booking), status=201)

@api_view(["POST"])
def booking_release(request, pk):
    """Release a held booking immediately."""
    try:
        booking = release_hold(pk)
    except Booking.DoesNotExist:
        return Response({"detail": "Booking not found."}, status=404)
    except SlotTaken as e:
        return Response({"detail": str(e)}, status=409)

    return Response(booking_json(booking))




@api_view(["GET"])
def booking_detail(request, pk):
    """The page polls this after paying, until the webhook has confirmed the booking."""
    return Response(booking_json(get_object_or_404(Booking, pk=pk)))


@api_view(["POST"])
def booking_pay(request, pk):
    """Create (or reuse) a Razorpay order for a held booking.

    Returns what the browser needs to open Razorpay Checkout. This does NOT
    confirm anything: only the webhook below can do that.
    """
    booking = get_object_or_404(Booking, pk=pk)
    if booking.status != Booking.Status.HELD or booking.hold_expires_at <= timezone.now():
        return Response({"detail": "Your hold has run out. Please pick a time again."}, status=409)

    payment = start_payment(booking)
    return Response({
        "key_id": settings.RAZORPAY_KEY_ID,
        "order_id": payment.razorpay_order_id,
        "amount": payment.amount_paise,
        "currency": "INR",
    })


@csrf_exempt  # Razorpay can't send a CSRF token; the signature check replaces it.
@require_POST
def razorpay_webhook(request):
    """Razorpay calls this when a payment succeeds. May be called many times per event."""
    # Verify against the RAW bytes: re-encoding parsed JSON could change them.
    if not signature_is_valid(request.body, request.headers.get("X-Razorpay-Signature", "")):
        return JsonResponse({"detail": "Invalid signature"}, status=400)
    event_id = request.headers.get("X-Razorpay-Event-Id")
    if not event_id:
        return JsonResponse({"detail": "Missing X-Razorpay-Event-Id"}, status=400)

    result = handle_webhook_event(event_id, json.loads(request.body))
    # Always 200 for a genuine event, even a duplicate, so Razorpay stops retrying.
    return JsonResponse({"result": result})