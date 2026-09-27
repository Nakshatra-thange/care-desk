from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import Q


class Therapist(models.Model):
    name = models.CharField(max_length=120)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")  # IANA name, e.g. "Europe/London"
    session_minutes = models.PositiveSmallIntegerField(default=50)

    def __str__(self):
        return self.name


class AvailabilityRule(models.Model):
    """A weekly window in the THERAPIST's local time, e.g. "Tuesdays 18:00-21:00".

    Stored as local wall-clock time, not UTC, because the UTC time of
    "18:00 in London" changes when the clocks change.
    """

    class Weekday(models.IntegerChoices):
        MONDAY = 0
        TUESDAY = 1
        WEDNESDAY = 2
        THURSDAY = 3
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

    therapist = models.ForeignKey(Therapist, on_delete=models.CASCADE, related_name="availability_rules")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"{self.therapist} {self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"


class Client(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)

    def __str__(self):
        return self.name


class Booking(models.Model):
    class Status(models.TextChoices):
        HELD = "held"            # reserved while the client pays (Day 3)
        CONFIRMED = "confirmed"  # paid, or a free booking
        CANCELLED = "cancelled"
        EXPIRED = "expired"      # a hold that ran out of time (Day 3)

    therapist = models.ForeignKey(Therapist, on_delete=models.PROTECT, related_name="bookings")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="bookings")
    # A Postgres tstzrange: [start, end). Start is included, end is not,
    # so a 10:00-11:00 booking and an 11:00-12:00 booking do NOT overlap.
    during = DateTimeRangeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    # Only set while status is "held": when the client's 10 minutes to pay run out.
    hold_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # IDEA 1: for the same therapist, no two active bookings may overlap.
            # Postgres itself enforces this, even for requests arriving at the same instant.
            ExclusionConstraint(
                name="no_overlapping_bookings",
                expressions=[
                    ("therapist", RangeOperators.EQUAL),
                    ("during", RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=["held", "confirmed"]),
            ),
        ]

    def __str__(self):
        return f"{self.client} with {self.therapist} ({self.status})"