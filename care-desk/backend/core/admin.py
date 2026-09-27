from django.contrib import admin

from .models import AvailabilityRule, Booking, Client, Therapist


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