"""Every function that writes a booking lives here.

Idea 1 (Day 1): lock the therapist's row first, so bookings for one therapist
take turns, and let the exclusion constraint be the final safety net.

Idea 3 (Day 3): a hold lasts 10 minutes. Nothing runs in the background to
expire it. Instead, every write first marks this therapist's out-of-time holds
as "expired", inside the same locked transaction. So a stale hold can never
block anyone, and correctness never depends on a job running on time.
"""
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
    """Mark this therapist's holds whose time is up as 'expired'. Returns how many.

    This must run before inserting: the exclusion constraint only looks at the
    status column, so a stale hold still marked 'held' would block the slot.
    """
    return Booking.objects.filter(
        therapist=therapist, status=Booking.Status.HELD, hold_expires_at__lte=now
    ).update(status=Booking.Status.EXPIRED)


@transaction.atomic
def create_booking(client: Client, therapist_id: int, start: datetime) -> Booking:
    """Book a slot directly as confirmed, with no payment step (used on Day 1)."""
    therapist = _lock_therapist(therapist_id)
    return _insert(therapist, client, start, status=Booking.Status.CONFIRMED)


@transaction.atomic
def hold_slot(client: Client, therapist_id: int, start: datetime, now: datetime | None = None) -> Booking:
    """Reserve a slot for 10 minutes while the client pays."""
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
    """Turn a held booking into a confirmed one once payment arrives.

    Safe to call twice: confirming a confirmed booking changes nothing.
    If the payment arrives after the hold ran out, we still confirm when the
    slot is free, and raise SlotTaken when someone else has taken it.
    """
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