
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.utils import timezone

from .models import Booking, Client, Therapist
from .scheduling import get_open_slots

HOLD_DURATION = timedelta(minutes=10)


class SlotTaken(Exception):
    pass


def _lock_therapist(therapist_id: int) -> Therapist:
    # Waits here if another request is booking this therapist right now.
    return Therapist.objects.select_for_update().get(pk=therapist_id)


def _insert(therapist: Therapist, client: Client, start: datetime, **fields) -> Booking:
    end = start + timedelta(minutes=therapist.session_minutes)
    try:
        # Inner atomic = savepoint, so a failed insert doesn't break the outer transaction.
        with transaction.atomic():
            return Booking.objects.create(
                therapist=therapist, client=client, during=DateTimeTZRange(start, end), **fields
            )
    except IntegrityError:
        raise SlotTaken("Sorry, this slot was just taken. Please pick another time.")


def expire_stale_holds(therapist: Therapist, now: datetime) -> int:
   
    return Booking.objects.filter(
        therapist=therapist, status=Booking.Status.HELD, hold_expires_at__lte=now
    ).update(status=Booking.Status.EXPIRED)


@transaction.atomic
def create_booking(client: Client, therapist_id: int, start: datetime) -> Booking:
   
    therapist = _lock_therapist(therapist_id)
    return _insert(therapist, client, start, status=Booking.Status.CONFIRMED)


@transaction.atomic
def hold_slot(client: Client, therapist_id: int, start: datetime, now: datetime | None = None) -> Booking:

    now = now or timezone.now()
    therapist = _lock_therapist(therapist_id)
    expire_stale_holds(therapist, now)

    # Only allow times the schedule actually offers. Never trust a time sent by the browser.
    offered = get_open_slots(therapist, start, start + timedelta(minutes=1), now=now)
    if not any(slot.start == start for slot in offered):
        raise SlotTaken("Sorry, this slot was just taken. Please pick another time.")

    return _insert(
        therapist, client, start,
        status=Booking.Status.HELD,
        hold_expires_at=now + HOLD_DURATION,
    )


@transaction.atomic
def confirm_booking(booking_id: int, now: datetime | None = None) -> Booking:
   
    now = now or timezone.now()
    therapist_id = Booking.objects.values_list("therapist_id", flat=True).get(pk=booking_id)
    therapist = _lock_therapist(therapist_id)
    expire_stale_holds(therapist, now)
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.status == Booking.Status.CONFIRMED:
        return booking
    if booking.status == Booking.Status.CANCELLED:
        raise SlotTaken("This booking was cancelled.")

    booking.status = Booking.Status.CONFIRMED
    booking.hold_expires_at = None
    try:
        with transaction.atomic():
            booking.save(update_fields=["status", "hold_expires_at"])
    except IntegrityError:
        raise SlotTaken("Your hold ran out and someone else has booked this slot.")
    return booking



@transaction.atomic
def release_hold(booking_id: int) -> Booking:
    
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    if booking.status != Booking.Status.HELD:
        raise SlotTaken(f"Only a held slot can be released. This booking is {booking.status}.")
    booking.status = Booking.Status.CANCELLED
    booking.hold_expires_at = None
    booking.save(update_fields=["status", "hold_expires_at"])
    return booking