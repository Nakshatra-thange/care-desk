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
        HELD = "held"     
        CONFIRMED = "confirmed"  
        CANCELLED = "cancelled"
        EXPIRED = "expired"     

    therapist = models.ForeignKey(Therapist, on_delete=models.PROTECT, related_name="bookings")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="bookings")
    
    during = DateTimeRangeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            
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



class Payment(models.Model):
    """One Razorpay order for one booking."""

    class Status(models.TextChoices):
        CREATED = "created"            
        PAID = "paid"                 
        NEEDS_REFUND = "needs_refund" 

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="payments")
    razorpay_order_id = models.CharField(max_length=64, unique=True)
    razorpay_payment_id = models.CharField(max_length=64, blank=True)
    amount_paise = models.PositiveIntegerField()  # money as whole paise, never floats
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.razorpay_order_id} ({self.status})"


class WebhookEvent(models.Model):
   

    event_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event_type} {self.event_id}"