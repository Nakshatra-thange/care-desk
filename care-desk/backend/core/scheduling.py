"""Idea 2: turning weekly rules into real, bookable UTC slots.

The key point: "Tuesdays at 18:00" is a LOCAL time. Its UTC equivalent is
different on different dates whenever daylight saving changes, so we convert
one date at a time, never by adding a fixed offset.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone as dj_timezone

from .models import Booking, Therapist

UTC = timezone.utc


@dataclass(frozen=True, order=True)
class Slot:
    start: datetime  # UTC
    end: datetime    # UTC


def generate_slots(rules, tz: ZoneInfo, session_minutes: int, window_start: datetime, window_end: datetime) -> list[Slot]:
    """Every slot the rules produce that starts inside [window_start, window_end).

    Pure function: no database, so it's easy to test.

    For each of the therapist's LOCAL dates:
      1. turn that day's rule start and end into real UTC instants
      2. step from start to end in real minutes, one session at a time
    Stepping in real time (not wall-clock time) means slots can never overlap,
    even on the night the clocks change.
    """
    length = timedelta(minutes=session_minutes)
    slots = []

    # Add a day on each side: a UTC window can begin or end on a different local date.
    day = window_start.astimezone(tz).date() - timedelta(days=1)
    last_day = window_end.astimezone(tz).date() + timedelta(days=1)

    while day <= last_day:
        for rule in rules:
            if rule.weekday != day.weekday():
                continue
            # "18:00 on this date, in the therapist's timezone" -> a real UTC instant.
            # The offset is looked up for THIS date, so daylight saving is handled.
            start = datetime.combine(day, rule.start_time, tzinfo=tz).astimezone(UTC)
            rule_end = datetime.combine(day, rule.end_time, tzinfo=tz).astimezone(UTC)

            while start + length <= rule_end:
                if window_start <= start < window_end:
                    slots.append(Slot(start, start + length))
                start += length
        day += timedelta(days=1)

    return slots


def get_open_slots(therapist: Therapist, window_start: datetime, window_end: datetime, now: datetime | None = None) -> list[Slot]:
    """Slots from the rules, minus anything in the past or already taken.

    A hold whose 10 minutes have run out does NOT count as taken, even if
    nothing has marked it "expired" yet (Idea 3).
    """
    now = now or dj_timezone.now()
    candidates = generate_slots(
        rules=list(therapist.availability_rules.all()),
        tz=ZoneInfo(therapist.timezone),
        session_minutes=therapist.session_minutes,
        window_start=max(window_start, now),
        window_end=window_end,
    )

    taken = Q(status=Booking.Status.CONFIRMED) | Q(status=Booking.Status.HELD, hold_expires_at__gt=now)
    booked = list(Booking.objects.filter(
        taken,
        therapist=therapist,
        during__overlap=(window_start, window_end),
    ).values_list("during", flat=True))

    def is_free(slot):
        # Two ranges overlap if each starts before the other ends.
        return not any(slot.start < b.upper and b.lower < slot.end for b in booked)

    return [s for s in candidates if is_free(s)]