from django.contrib import admin

from .models import AvailabilityRule, Booking, Client, Payment, Therapist, WebhookEvent


class AvailabilityRuleInline(admin.TabularInline):
    model = AvailabilityRule
    extra = 1


@admin.register(Therapist)
class TherapistAdmin(admin.ModelAdmin):
    list_display = ["name", "timezone", "session_minutes"]
    inlines = [AvailabilityRuleInline]  # edit weekly hours on the therapist's page


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["client", "therapist", "during", "status"]
    list_filter = ["status", "therapist"]


admin.site.register(Client)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["razorpay_order_id", "booking", "amount_paise", "status", "created_at"]
    list_filter = ["status"]


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ["event_id", "event_type", "received_at"]