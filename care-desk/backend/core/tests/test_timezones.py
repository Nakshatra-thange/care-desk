"""Idea 2: timezones are handled correctly.

UK clocks in 2026:
  - Sun 29 Mar: clocks jump FORWARD, 01:00 -> 02:00 (that night is an hour shorter)
  - Sun 25 Oct: clocks go BACK, 02:00 -> 01:00 (that night is an hour longer)
India never changes its clocks.
"""
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.test import SimpleTestCase, TestCase

from core.models import AvailabilityRule, Booking, Client, Therapist
from core.scheduling import generate_slots, get_open_slots

IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
MON, TUE, SUN = 0, 1, 6


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def rule(weekday, start, end):
    """A stand-in for AvailabilityRule, so these tests don't need the database."""
    return SimpleNamespace(weekday=weekday, start_time=start, end_time=end)


def in_tz(slots, tz):
    return [s.start.astimezone(tz).strftime("%d %b %H:%M") for s in slots]


class GenerateSlotsTests(SimpleTestCase):
    def test_indian_therapist_seen_from_london_moves_when_uk_clocks_change(self):
        # Tuesdays 19:00-20:00 in India.
        slots = generate_slots([rule(TUE, time(19), time(20))], IST, 50, utc(2026, 10, 19), utc(2026, 11, 1))
        # Same UTC instant both weeks, because India never changes its clocks...
        self.assertEqual([s.start for s in slots], [utc(2026, 10, 20, 13, 30), utc(2026, 10, 27, 13, 30)])
        # ...but a London client sees 14:30 before the change and 13:30 after it.
        self.assertEqual(in_tz(slots, LONDON), ["20 Oct 14:30", "27 Oct 13:30"])

    def test_london_therapist_keeps_the_same_local_time_across_the_change(self):
        # Mondays 08:00-09:00 in London.
        slots = generate_slots([rule(MON, time(8), time(9))], LONDON, 50, utc(2026, 10, 19), utc(2026, 11, 1))
        # Always 08:00 on the therapist's own clock...
        self.assertEqual(in_tz(slots, LONDON), ["19 Oct 08:00", "26 Oct 08:00"])
        # ...which is 07:00 UTC in summer time and 08:00 UTC in winter time.
        self.assertEqual([s.start for s in slots], [utc(2026, 10, 19, 7), utc(2026, 10, 26, 8)])

    def test_short_night_in_spring_fits_fewer_sessions(self):
        # Sun 29 Mar, 00:00-03:00 on the clock, but only 2 real hours pass.
        slots = generate_slots([rule(SUN, time(0), time(3))], LONDON, 50, utc(2026, 3, 28, 12), utc(2026, 3, 29, 12))
        self.assertEqual([s.start for s in slots], [utc(2026, 3, 29, 0, 0), utc(2026, 3, 29, 0, 50)])

    def test_long_night_in_autumn_fits_more_sessions(self):
        # Sun 25 Oct, 00:00-03:00 on the clock, but 4 real hours pass.
        slots = generate_slots([rule(SUN, time(0), time(3))], LONDON, 50, utc(2026, 10, 24, 12), utc(2026, 10, 25, 12))
        self.assertEqual(len(slots), 4)

    def test_slots_never_overlap_and_are_always_50_real_minutes(self):
        for window in [(utc(2026, 3, 28), utc(2026, 3, 30)), (utc(2026, 10, 24), utc(2026, 10, 26))]:
            slots = generate_slots([rule(SUN, time(0), time(3))], LONDON, 50, *window)
            for s in slots:
                self.assertEqual(s.end - s.start, timedelta(minutes=50))
            for a, b in zip(slots, slots[1:]):
                self.assertLessEqual(a.end, b.start)

    def test_only_slots_starting_inside_the_window(self):
        rules = [rule(d, time(10), time(11)) for d in range(7)]  # every day
        slots = generate_slots(rules, IST, 50, utc(2026, 10, 20), utc(2026, 10, 23))
        self.assertEqual(len(slots), 3)


class OpenSlotsTests(TestCase):
    def setUp(self):
        # Tuesdays 18:00-21:00 India -> 12:30, 13:20, 14:10 UTC on 20 Oct.
        self.therapist = Therapist.objects.create(name="Asha", timezone="Asia/Kolkata", session_minutes=50)
        AvailabilityRule.objects.create(therapist=self.therapist, weekday=TUE, start_time=time(18), end_time=time(21))
        self.client_ = Client.objects.create(name="Priya", email="priya@example.com")
        self.window = (utc(2026, 10, 20), utc(2026, 10, 21))
        self.now = utc(2026, 10, 1)

    def starts(self, now=None):
        return [s.start for s in get_open_slots(self.therapist, *self.window, now=now or self.now)]

    def book(self, start, status=Booking.Status.CONFIRMED):
        Booking.objects.create(
            therapist=self.therapist, client=self.client_, status=status,
            during=DateTimeTZRange(start, start + timedelta(minutes=50)),
        )

    def test_all_slots_open_on_an_empty_calendar(self):
        self.assertEqual(self.starts(), [utc(2026, 10, 20, 12, 30), utc(2026, 10, 20, 13, 20), utc(2026, 10, 20, 14, 10)])

    def test_a_booked_slot_disappears(self):
        self.book(utc(2026, 10, 20, 13, 20))
        self.assertNotIn(utc(2026, 10, 20, 13, 20), self.starts())

    def test_a_cancelled_booking_does_not_block(self):
        self.book(utc(2026, 10, 20, 13, 20), status=Booking.Status.CANCELLED)
        self.assertEqual(len(self.starts()), 3)

    def test_past_slots_are_hidden(self):
        self.assertEqual(self.starts(now=utc(2026, 10, 20, 13, 0)), [utc(2026, 10, 20, 13, 20), utc(2026, 10, 20, 14, 10)])


@mock.patch("core.scheduling.dj_timezone.now", return_value=utc(2026, 10, 1))
class SlotsApiTests(TestCase):
    def setUp(self):
        self.therapist = Therapist.objects.create(name="Asha", timezone="Asia/Kolkata")
        AvailabilityRule.objects.create(therapist=self.therapist, weekday=TUE, start_time=time(19), end_time=time(20))
        self.url = f"/api/therapists/{self.therapist.pk}/slots/"

    def test_london_client_gets_local_times(self, _now):
        res = self.client.get(self.url, {"start_date": "2026-10-19", "end_date": "2026-10-31", "tz": "Europe/London"})
        self.assertEqual(res.status_code, 200)
        slots = res.json()["slots"]
        self.assertEqual([(s["local_date"], s["local_time"]) for s in slots], [("2026-10-20", "14:30"), ("2026-10-27", "13:30")])
        self.assertEqual(slots[0]["therapist_time"], "19:00")
        self.assertEqual(slots[0]["start"], "2026-10-20T13:30:00Z")

    def test_unknown_timezone_is_rejected(self, _now):
        res = self.client.get(self.url, {"start_date": "2026-10-19", "end_date": "2026-10-20", "tz": "Mars/Base"})
        self.assertEqual(res.status_code, 400)

    def test_missing_dates_are_rejected(self, _now):
        self.assertEqual(self.client.get(self.url).status_code, 400)