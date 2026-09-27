"""python manage.py seed -> wipes and recreates a small set of FAKE demo data."""

from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import AvailabilityRule, Booking, Client, Therapist


MON, TUE, WED, THU, FRI, SAT = range(6)


THERAPISTS = [
    # name, email, timezone, [(weekdays, start, end)] in therapist's local time
    (
        "Asha Menon",
        "asha.menon@example.com",
        "Asia/Kolkata",
        [([MON, WED, FRI], time(18, 0), time(21, 0))],
    ),
    (
        "Farah Qureshi",
        "farah.qureshi@example.com",
        "Asia/Dubai",
        [([TUE, THU], time(10, 0), time(14, 0))],
    ),
    (
        "Tom Ellis",
        "tom.ellis@example.com",
        "Europe/London",
        [
            ([MON, TUE, WED], time(8, 0), time(11, 0)),
            ([SAT], time(9, 0), time(12, 0)),
        ],
    ),
]


CLIENTS = [
    ("Priya Sharma", "priya@example.com"),
    ("Karan Mehta", "karan@example.com"),
    ("Neha Iyer", "neha@example.com"),
]


class Command(BaseCommand):
    help = "Reset the database to a small set of fake demo data."

    @transaction.atomic
    def handle(self, *args, **options):
        Booking.objects.all().delete()
        Therapist.objects.all().delete()
        Client.objects.all().delete()

        for name, email, tz, windows in THERAPISTS:
            therapist = Therapist.objects.create(name=name, timezone=tz)

            for weekdays, start, end in windows:
                for wd in weekdays:
                    AvailabilityRule.objects.create(
                        therapist=therapist,
                        weekday=wd,
                        start_time=start,
                        end_time=end,
                    )

        for name, email in CLIENTS:
            Client.objects.create(
                name=name,
                email=email,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(THERAPISTS)} therapists and {len(CLIENTS)} clients."
            )
        )