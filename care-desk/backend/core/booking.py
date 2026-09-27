"""The one function that creates bookings.

Two layers of protection against double-booking:
  1. Lock the therapist's row first, so bookings for the same therapist
     happen one at a time. Without this, concurrent inserts can deadlock.
  2. The exclusion constraint in models.py, the final safety net.
"""
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange

from .models import Booking, Client, Therapist


class SlotTaken(Exception):
    pass


@transaction.atomic
def create_booking(client: Client, therapist_id: int, start: datetime) -> Booking:
    # Waits here if another request is booking this therapist right now.
    therapist = Therapist.objects.select_for_update().get(pk=therapist_id)
    end = start + timedelta(minutes=therapist.session_minutes)

    try:
        # Inner atomic = savepoint, so a failed insert doesn't break the outer transaction.
        with transaction.atomic():
            return Booking.objects.create(
                therapist=therapist,
                client=client,
                during=DateTimeTZRange(start, end),
                status=Booking.Status.CONFIRMED,
            )
    except IntegrityError:
        raise SlotTaken("This slot was just taken.")