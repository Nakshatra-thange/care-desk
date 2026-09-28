# Care Desk

A booking system for a therapy practice with clients around the world, built to get
four hard things right. All data is fake.

Stack: Django + Django REST Framework, PostgreSQL, Next.js + TypeScript, Razorpay (test mode).

## The four ideas

### 1. Double-booking is impossible
Checking "is this slot free?" and then inserting has a race: two requests can both pass
the check. Instead, `Booking.during` is a Postgres time range, and an **exclusion
constraint** forbids two active bookings for one therapist from overlapping. Postgres
enforces it even for simultaneous requests.

While testing, 8 simultaneous requests sometimes **deadlocked**: each insert waits to see
whether the others commit, and two losers can wait on each other. So every booking write
first **locks the therapist's row**, making requests for one therapist take turns. The
constraint stays as the safety net.
Code: `core/models.py`, `core/booking.py`. Proof: `core/tests/test_double_booking.py`.

### 2. Timezones are handled correctly
Therapists set hours in their own local time ("Tuesdays 18:00 in India"). Slots are built
one local date at a time and converted to UTC for that exact date, so daylight saving is
handled. Slots step forward in real minutes, so they never overlap, even on the nights
the clocks change. Everything is stored in UTC; the browser shows local time.
Code: `core/scheduling.py`. Proof: `core/tests/test_timezones.py`.

### 3. Holds expire without a background job
A slot is held for 10 minutes while the client pays. Nothing runs in the background to
expire holds. Instead, every booking write first expires the therapist's out-of-time holds
inside the same locked transaction, and slot listings ignore timed-out holds. Correctness
never depends on a job running on time, and there's nothing extra to deploy.
Code: `core/booking.py`. Proof: `core/tests/test_holds.py`.

### 4. Payments can't be processed twice
The browser never confirms a booking. Razorpay sends a signed webhook to the server, and
only that confirms it. Razorpay may deliver the same event several times, so:
- `WebhookEvent.event_id` is unique: a repeated event can't be stored or processed twice.
- Recording the event and acting on it happen in one transaction.
- Wrong amounts and payments for slots lost after a hold expired are flagged `needs_refund`.

Code: `core/payments.py`. Proof: `core/tests/test_payments.py`
(including 5 identical deliveries at the same instant: processed once).

## Run it locally

```bash
docker compose up -d db
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add your Razorpay TEST keys
python manage.py migrate
python manage.py test core
python manage.py seed
python manage.py runserver
```

In another terminal:

```bash
cd frontend
npm install
npm run dev                      # http://localhost:3000
```

Replay one webhook several times to see idempotency:
`python manage.py replay_webhook --times 3`

## What I'd build next
Pricing by country of residence, cancellations and refunds via the Razorpay API,
email reminders, and a dashboard for the team that supports clients.