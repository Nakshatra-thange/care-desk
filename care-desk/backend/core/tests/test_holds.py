"""Idea 3: holds expire without any background job.

Asha works Tuesdays 18:00-21:00 India time, so on Tue 20 Oct 2026 her slots
start at 12:30, 13:20 and 14:10 UTC. "Now" is 1 Oct unless a test says otherwise.
"""
import threading
from datetime import datetime, time, timedelta, timezone
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase

from core.booking import SlotTaken, confirm_booking, hold_slot
from core.models import AvailabilityRule, Booking, Client, Therapist
from core.scheduling import get_open_slots

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
SLOT = datetime(2026, 10, 20, 13, 20, tzinfo=timezone.utc)
TEN_MIN = timedelta(minutes=10)


def setup_asha():
    asha = Therapist.objects.create(name="Asha", timezone="Asia/Kolkata", session_minutes=50)
    AvailabilityRule.objects.create(therapist=asha, weekday=1, start_time=time(18), end_time=time(21))
    return asha


def make_client(n):
    return Client.objects.create(name=f"Client {n}", email=f"client{n}@example.com")


class HoldTests(TestCase):
    def setUp(self):
        self.asha = setup_asha()
        self.priya = make_client(1)
        self.karan = make_client(2)

    def test_a_hold_lasts_ten_minutes(self):
        b = hold_slot(self.priya, self.asha.pk, SLOT, now=NOW)
        self.assertEqual(b.status, Booking.Status.HELD)
        self.assertEqual(b.hold_expires_at, NOW + TEN_MIN)

    def test_a_time_the_schedule_does_not_offer_is_rejected(self):
        with self.assertRaises(SlotTaken):
            hold_slot(self.priya, self.asha.pk, SLOT + timedelta(minutes=5), now=NOW)

    def test_a_live_hold_blocks_everyone_else(self):
        hold_slot(self.priya, self.asha.pk, SLOT, now=NOW)
        with self.assertRaises(SlotTaken):
            hold_slot(self.karan, self.asha.pk, SLOT, now=NOW + timedelta(minutes=9))

    def test_after_ten_minutes_someone_else_can_take_the_slot(self):
        """The core of Idea 3: no background job ran, yet the slot is free again."""
        first = hold_slot(self.priya, self.asha.pk, SLOT, now=NOW)
        second = hold_slot(self.karan, self.asha.pk, SLOT, now=NOW + timedelta(minutes=11))

        first.refresh_from_db()
        self.assertEqual(first.status, Booking.Status.EXPIRED)  # expired during Karan's request
        self.assertEqual(second.status, Booking.Status.HELD)

    def test_a_stale_hold_is_shown_as_free_before_anything_expires_it(self):
        hold_slot(self.priya, self.asha.pk, SLOT, now=NOW)
        window = (SLOT, SLOT + timedelta(minutes=1))  # just the held slot
        self.assertEqual(len(get_open_slots(self.asha, *window, now=NOW + timedelta(minutes=5))), 0)
        self.assertEqual(len(get_open_slots(self.asha, *window, now=NOW + timedelta(minutes=11))), 1)


class ConfirmTests(TestCase):
    def setUp(self):
        self.asha = setup_asha()
        self.priya = make_client(1)
        self.karan = make_client(2)
        self.hold = hold_slot(self.priya, self.asha.pk, SLOT, now=NOW)

    def test_paying_in_time_confirms(self):
        b = confirm_booking(self.hold.pk, now=NOW + timedelta(minutes=5))
        self.assertEqual(b.status, Booking.Status.CONFIRMED)
        self.assertIsNone(b.hold_expires_at)

    def test_confirming_twice_changes_nothing(self):
        confirm_booking(self.hold.pk, now=NOW)
        b = confirm_booking(self.hold.pk, now=NOW)
        self.assertEqual(b.status, Booking.Status.CONFIRMED)

    def test_late_payment_still_confirms_if_nobody_took_the_slot(self):
        b = confirm_booking(self.hold.pk, now=NOW + timedelta(minutes=20))
        self.assertEqual(b.status, Booking.Status.CONFIRMED)

    def test_late_payment_fails_if_someone_else_took_the_slot(self):
        later = NOW + timedelta(minutes=20)
        hold_slot(self.karan, self.asha.pk, SLOT, now=later)
        with self.assertRaises(SlotTaken):
            confirm_booking(self.hold.pk, now=later)


class ConcurrentHoldTests(TransactionTestCase):
    def test_eight_clients_one_slot_one_hold(self):
        asha = setup_asha()
        clients = [make_client(i) for i in range(8)]
        barrier = threading.Barrier(len(clients))
        results, lock = [], threading.Lock()

        def worker(client):
            try:
                barrier.wait()
                hold_slot(client, asha.pk, SLOT, now=NOW)
                outcome = "held"
            except SlotTaken:
                outcome = "taken"
            except Exception as e:
                outcome = f"error: {e!r}"
            finally:
                connection.close()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker, args=(c,)) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count("held"), 1, results)
        self.assertEqual(results.count("taken"), 7, results)


@mock.patch("django.utils.timezone.now", return_value=NOW)
class HoldApiTests(TestCase):
    def setUp(self):
        self.asha = setup_asha()
        self.priya = make_client(1)
        self.karan = make_client(2)

    def post_hold(self, client):
        return self.client.post(
            "/api/bookings/hold/",
            {"client_id": client.pk, "therapist_id": self.asha.pk, "start": "2026-10-20T13:20:00Z"},
            content_type="application/json",
        )

    def test_hold_then_second_client_gets_409(self, _now):
        res = self.post_hold(self.priya)
        self.assertEqual(res.status_code, 201, res.json())
        self.assertEqual(res.json()["hold_expires_at"], "2026-10-01T09:10:00Z")

        res2 = self.post_hold(self.karan)
        self.assertEqual(res2.status_code, 409)
        self.assertIn("just taken", res2.json()["detail"])

        

    def test_bad_start_is_rejected(self, _now):
        res = self.client.post(
            "/api/bookings/hold/",
            {"client_id": self.priya.pk, "therapist_id": self.asha.pk, "start": "tomorrow"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)