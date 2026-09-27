from django.contrib import admin

from .models import Booking, Client, Therapist

admin.site.register(Therapist)
admin.site.register(Client)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["client", "therapist", "during", "status"]
    list_filter = ["status", "therapist"]