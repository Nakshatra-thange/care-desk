"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api, type Booking, type Person, type Slot } from "@/lib/api";
import { openCheckout } from "@/lib/razorpay";

const DAYS_AHEAD = 14;

// ---------- small helpers ----------

// The viewer's timezone, e.g. "Europe/London". null while rendering on the server.
function useTimezone() {
  return useSyncExternalStore(
    () => () => {},
    () => Intl.DateTimeFormat().resolvedOptions().timeZone,
    () => null,
  );
}

// Today (plus some days) as YYYY-MM-DD in a timezone. The en-CA locale formats dates that way.
function localDate(tz: string, addDays = 0) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date(Date.now() + addDays * 86_400_000));
}

function dayLabel(ymd: string) {
  return new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).format(
    new Date(`${ymd}T12:00:00Z`),
  );
}

function when(iso: string, tz: string) {
  return new Intl.DateTimeFormat("en-IN", {
    weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", timeZone: tz,
  }).format(new Date(iso));
}

const city = (tz: string) => tz.split("/").pop()!.replaceAll("_", " ");

const rupees = (paise: number) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(paise / 100);

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---------- the page ----------

export default function BookingPage() {
  const tz = useTimezone();

  const [clients, setClients] = useState<Person[]>([]);
  const [therapists, setTherapists] = useState<Person[]>([]);
  const [clientId, setClientId] = useState<number | null>(null);
  const [therapistId, setTherapistId] = useState<number | null>(null);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [day, setDay] = useState<string | null>(null);
  const [hold, setHold] = useState<Booking | null>(null);
  const [confirmed, setConfirmed] = useState<Booking | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [waitingForWebhook, setWaitingForWebhook] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  // Until the user picks, default to the first client and therapist.
  const client = clientId ?? clients[0]?.id ?? null;
  const therapistPk = therapistId ?? therapists[0]?.id ?? null;
  const therapist = therapists.find((t) => t.id === therapistPk);

  useEffect(() => {
    api.clients().then(setClients).catch((e) => setNotice(e.message));
    api.therapists().then(setTherapists).catch((e) => setNotice(e.message));
  }, []);

  // Load open slots whenever the therapist changes, or after a hold/expiry.
  useEffect(() => {
    if (!tz || !therapistPk) return;
    let ignore = false;
    api
      .slots(therapistPk, localDate(tz), localDate(tz, DAYS_AHEAD - 1), tz)
      .then((res) => !ignore && setSlots(res.slots))
      .catch((e) => !ignore && setNotice(e.message));
    return () => {
      ignore = true;
    };
  }, [tz, therapistPk, reloadKey]);

  const days = useMemo(() => [...new Set(slots.map((s) => s.local_date))], [slots]);
  const selectedDay = day && days.includes(day) ? day : days[0];
  const reloadSlots = () => setReloadKey((k) => k + 1);

  async function pick(slot: Slot) {
    if (!client || !therapistPk) return;
    setBusy(true);
    setNotice(null);
    try {
      setHold(await api.hold(client, therapistPk, slot.start));
    } catch (e) {
      setNotice((e as Error).message); // e.g. "Sorry, this slot was just taken..."
      reloadSlots();
    } finally {
      setBusy(false);
    }
  }

  async function pay() {
    if (!hold) return;
    setBusy(true);
    setNotice(null);
    try {
      const order = await api.pay(hold.id);
      const result = await openCheckout(order, `Session with ${hold.therapist}`);
      if (result === "closed") {
        setNotice("Payment window closed. Your slot stays held until the timer runs out.");
        return;
      }
      // The browser says it worked, but only the webhook confirms. Poll until it has.
      setWaitingForWebhook(true);
      for (let i = 0; i < 30; i++) {
        const latest = await api.booking(hold.id);
        if (latest.status === "confirmed") {
          setConfirmed(latest);
          setHold(null);
          return;
        }
        await sleep(2000);
      }
      setNotice("Payment received. Still waiting for Razorpay's confirmation; check back in a minute.");
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
      setWaitingForWebhook(false);
    }
  }

  function holdRanOut() {
    if (waitingForWebhook) return; // paid already: a late payment is still confirmed if the slot is free
    setHold(null);
    setNotice("Your 10 minutes ran out, so the slot was released. Please pick a time again.");
    reloadSlots();
  }

  if (!tz) return null;

  return (
    <main className="mx-auto max-w-3xl px-5 py-10">
      <h1 className="text-2xl font-bold">Book a session</h1>
      <p className="mt-1 text-sm text-gray-600">
        Times are shown in your timezone ({city(tz)}). The small grey time is the therapist&apos;s own local time.
      </p>

      {/* Who is booking, and with whom */}
      <div className="mt-6 flex flex-wrap gap-4">
        <label className="text-sm">
          Booking as
          <select
            className="ml-2 rounded border border-gray-300 bg-white px-2 py-1"
            value={client ?? ""}
            onChange={(e) => setClientId(Number(e.target.value))}
            disabled={!!hold}
          >
            {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label className="text-sm">
          Therapist
          <select
            className="ml-2 rounded border border-gray-300 bg-white px-2 py-1"
            value={therapistPk ?? ""}
            onChange={(e) => {
              setTherapistId(Number(e.target.value));
              setConfirmed(null);
            }}
            disabled={!!hold}
          >
            {therapists.map((t) => (
              <option key={t.id} value={t.id}>{t.name} ({city(t.timezone ?? "")})</option>
            ))}
          </select>
        </label>
      </div>

      {notice && <p className="mt-4 rounded bg-red-50 p-3 text-sm text-red-700">{notice}</p>}

      {confirmed && (
        <p className="mt-4 rounded bg-emerald-50 p-3 text-emerald-800">
          Confirmed: {confirmed.therapist}, {when(confirmed.start, tz)}.
        </p>
      )}

      {hold ? (
        <HoldPanel hold={hold} tz={tz} busy={busy} waiting={waitingForWebhook} onPay={pay} onRanOut={holdRanOut} />
      ) : (
        <>
          {/* Day picker */}
          <div className="mt-6 flex gap-2 overflow-x-auto pb-2">
            {days.map((d) => (
              <button
                key={d}
                onClick={() => setDay(d)}
                className={`shrink-0 rounded border px-3 py-2 text-sm ${
                  d === selectedDay ? "border-teal-700 bg-teal-50 font-semibold" : "border-gray-300 bg-white"
                }`}
              >
                {dayLabel(d)}
              </button>
            ))}
          </div>
          {days.length === 0 && <p className="mt-6 text-gray-600">No open times in the next {DAYS_AHEAD} days.</p>}

          {/* Slots for the chosen day */}
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {slots
              .filter((s) => s.local_date === selectedDay)
              .map((s) => (
                <button
                  key={s.start}
                  disabled={busy}
                  onClick={() => pick(s)}
                  className="rounded border border-gray-300 bg-white px-3 py-3 text-left hover:border-teal-700 disabled:opacity-50"
                >
                  <span className="block text-lg font-semibold">{s.local_time}</span>
                  <span className="block text-xs text-gray-500">
                    {s.therapist_time} in {city(therapist?.timezone ?? "")}
                  </span>
                </button>
              ))}
          </div>
        </>
      )}
    </main>
  );
}

// ---------- the 10-minute hold ----------

function HoldPanel({ hold, tz, busy, waiting, onPay, onRanOut }: {
  hold: Booking;
  tz: string;
  busy: boolean;
  waiting: boolean;
  onPay: () => void;
  onRanOut: () => void;
}) {
  const expiresAt = new Date(hold.hold_expires_at!).getTime();
  const [msLeft, setMsLeft] = useState(() => Math.max(0, expiresAt - Date.now()));

  useEffect(() => {
    const timer = setInterval(() => {
      const left = Math.max(0, expiresAt - Date.now());
      setMsLeft(left);
      if (left === 0) {
        clearInterval(timer);
        onRanOut();
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [expiresAt, onRanOut]);

  const minutes = Math.floor(msLeft / 60_000);
  const seconds = String(Math.floor((msLeft % 60_000) / 1000)).padStart(2, "0");

  return (
    <section className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
      <p className="inline-block rounded bg-amber-100 px-2 py-1 text-sm font-semibold text-amber-800">
        Held for you: {minutes}:{seconds}
      </p>
      <p className="mt-3 font-semibold">{hold.therapist}</p>
      <p>{when(hold.start, tz)}</p>
      <p className="mt-2 text-sm text-gray-600">Nobody else can book this slot while the timer runs.</p>
      <button
        onClick={onPay}
        disabled={busy}
        className="mt-5 rounded bg-teal-700 px-5 py-2.5 font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
      >
        {waiting ? "Confirming payment…" : `Pay ${rupees(hold.price_paise)}`}
      </button>
    </section>
  );
}