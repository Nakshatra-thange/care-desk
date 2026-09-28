from django.urls import path

from . import views

urlpatterns = [
    path("therapists/", views.therapist_list),
    path("therapists/<int:pk>/slots/", views.therapist_slots),
    path("clients/", views.client_list),
    path("bookings/hold/", views.booking_hold),
    path("bookings/<int:pk>/", views.booking_detail),
    path("bookings/<int:pk>/pay/", views.booking_pay),
    path("bookings/<int:pk>/release/", views.booking_release),
    path("webhooks/razorpay/", views.razorpay_webhook),
]