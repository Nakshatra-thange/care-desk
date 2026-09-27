"""Send the SAME signed "payment.captured" event to your local server several times.

    python manage.py replay_webhook             # the most recent payment
    python manage.py replay_webhook 12 --times 5  # the payment for booking 12

Use it to demo Idea 4 without exposing your laptop to the internet:
the first delivery confirms the booking, the rest come back as "duplicate".
"""
import hashlib
import hmac
import json

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Payment


class Command(BaseCommand):
    help = "Replay one signed payment.captured webhook several times."

    def add_arguments(self, parser):
        parser.add_argument("booking_id", type=int, nargs="?", help="Defaults to the most recent payment")
        parser.add_argument("--times", type=int, default=3)
        parser.add_argument("--url", default="http://localhost:8000/api/webhooks/razorpay/")

    def handle(self, *args, booking_id, times, url, **options):
        payments = Payment.objects.order_by("-created_at")
        if booking_id is not None:
            payments = payments.filter(booking_id=booking_id)
        payment = payments.first()
        if payment is None:
            raise CommandError("No payment for that booking. Click Pay on the page first, then close the Razorpay window.")
        if not settings.RAZORPAY_WEBHOOK_SECRET:
            raise CommandError("Set RAZORPAY_WEBHOOK_SECRET in backend/.env first.")

        body = json.dumps({
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": f"pay_replay_{payment.pk}",
                "order_id": payment.razorpay_order_id,
                "amount": payment.amount_paise,
            }}},
        }).encode()
        signature = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": f"evt_replay_{payment.pk}",  # the SAME id every time
        }

        for i in range(1, times + 1):
            res = requests.post(url, data=body, headers=headers, timeout=10)
            self.stdout.write(f"Delivery {i}: HTTP {res.status_code} {res.json()}")