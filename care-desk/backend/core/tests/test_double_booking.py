"""Idea 1: double-booking is impossible.

Run with:  python manage.py test core
"""
import threading
from datetime import datetime, timedelta, timezone

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.test import TestCase, TransactionTestCase

from core.booking import SlotTaken, create_booking
from core.models import Booking, Client, Therapist

START = datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)


def make_client(n):
    return Client.objects.create(name=f"Client {n}", email=f"client{n}@example.com")


def insert(therapist, client, start, minutes=50, status=Booking.Status.CONFIRMED):
    """A raw insert with no locking, to test the constraint on its own."""
    return Booking.objects.create(
        therapist=therapist,
        client=client,
        during=DateTimeTZRange(start, start + timedelta(minutes=minutes)),
        status=status,
    )


class ConstraintTests(TestCase):
    def setUp(self):
        self.therapist = Therapist.objects.create(name="Asha")
        self.alice = make_client(1)
        self.bob = make_client(2)

    def test_overlapping_booking_is_rejected(self):
        insert(self.therapist, self.alice, START)
        with self.assertRaises(IntegrityError), transaction.atomic():
            insert(self.therapist, self.bob, START + timedelta(minutes=20))  # starts inside Alice's session

    def test_back_to_back_bookings_are_allowed(self):
        insert(self.therapist, self.alice, START)                           # 13:30-14:20
        insert(self.therapist, self.bob, START + timedelta(minutes=50))     # 14:20-15:10
        self.assertEqual(Booking.objects.count(), 2)

    def test_different_therapists_can_have_the_same_time(self):
        other = Therapist.objects.create(name="Ravi")
        insert(self.therapist, self.alice, START)
        insert(other, self.bob, START)
        self.assertEqual(Booking.objects.count(), 2)

    def test_cancelled_booking_frees_the_slot(self):
        insert(self.therapist, self.alice, START, status=Booking.Status.CANCELLED)
        insert(self.therapist, self.bob, START)
        self.assertEqual(Booking.objects.count(), 2)

    def test_create_booking_raises_slot_taken(self):
        create_booking(self.alice, self.therapist.pk, START)
        with self.assertRaises(SlotTaken):
            create_booking(self.bob, self.therapist.pk, START)


class ConcurrencyTests(TransactionTestCase):
    """8 clients click "Book" on the same slot at the same instant.

    TransactionTestCase is needed: the normal TestCase wraps the whole test
    in one transaction, so threads could never really race.
    """

    CLIENTS = 8

    def race(self, book):
        therapist = Therapist.objects.create(name="Asha")
        clients = [make_client(i) for i in range(self.CLIENTS)]
        barrier = threading.Barrier(self.CLIENTS)  # releases every thread at once
        results, lock = [], threading.Lock()

        def worker(client):
            try:
                barrier.wait()
                book(therapist, client)
                outcome = "booked"
            except (IntegrityError, SlotTaken):
                outcome = "rejected"
            except OperationalError as e:
                outcome = "deadlock" if "deadlock" in str(e) else f"error: {e}"
            except Exception as e:
                outcome = f"error: {e!r}"
            finally:
                connection.close()  # each thread opened its own DB connection
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker, args=(c,)) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_raw_inserts_never_double_book(self):
        """Constraint only, no locking.

        Only one booking survives. But losers may see a DEADLOCK instead of a
        clean rejection: each insert waits to see if the others will commit,
        and two losers can end up waiting on each other. Postgres notices and
        aborts one of them. Safe, but messy.
        """
        def book(therapist, client):
            with transaction.atomic():
                insert(therapist, client, START)

        results = self.race(book)
        self.assertEqual(results.count("booked"), 1, results)
        self.assertTrue(all(r in ("booked", "rejected", "deadlock") for r in results), results)

    def test_create_booking_gives_one_winner_and_clean_rejections(self):
        """Lock the therapist, then insert: requests take turns, so no deadlocks."""
        def book(therapist, client):
            create_booking(client, therapist.pk, START)

        results = self.race(book)
        self.assertEqual(results.count("booked"), 1, results)
        self.assertEqual(results.count("rejected"), self.CLIENTS - 1, results)