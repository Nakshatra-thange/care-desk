"""Letting a client give back a held slot without waiting 10 minutes."""
from unittest import mock

from django.test import TestCase, override_settings

from core.booking import SlotTaken, confirm_booking, hold_slot, release_hold
from core.models import Booking, Payment
from core.tests.test_payments import NOW, SECRET, SLOT, WebhookHelpers, captured_event, make_client, setup_asha


@override_settings(RAZORPAY_WEBHOOK_SECRET=SECRET)
@mock.patch("django.utils.timezone.now", return_value=NOW)
class ReleaseTests(WebhookHelpers, TestCase):
    def setUp(self):
        self.asha = setup_asha()
        self.priya = make_client(1)
        self.booking = hold_slot(self.priya, self.asha.pk, SLOT, now=NOW)

    def test_released_slot_is_free_for_someone_else_immediately(self, _now):
        release_hold(self.booking.pk)
        other = hold_slot(make_client(2), self.asha.pk, SLOT, now=NOW)  # same minute, no waiting
        self.assertEqual(other.status, Booking.Status.HELD)

    def test_a_confirmed_booking_cannot_be_released(self, _now):
        confirm_booking(self.booking.pk, now=NOW)
        with self.assertRaises(SlotTaken):
            release_hold(self.booking.pk)

    def test_payment_arriving_after_release_is_flagged_for_refund(self, _now):
        Payment.objects.create(booking=self.booking, razorpay_order_id="order_TEST123", amount_paise=250000)
        release_hold(self.booking.pk)
        res = self.deliver(captured_event())
        self.assertEqual(res.json()["result"], "needs_refund")

    def test_release_via_api(self, _now):
        res = self.client.post(f"/api/bookings/{self.booking.pk}/release/")
        self.assertEqual(res.json()["status"], "cancelled")
        res2 = self.client.post(f"/api/bookings/{self.booking.pk}/release/")
        self.assertEqual(res2.status_code, 409)