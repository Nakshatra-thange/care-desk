

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

export type Person = { id: number; name: string; timezone?: string };

export type Slot = {
  start: string; 
  end: string;
  local_date: string;
  local_time: string; 
  therapist_time: string; 
};

export type PaymentOrder = { key_id: string; order_id: string; amount: number; currency: string };

export type Booking = {
  id: number;
  status: "held" | "confirmed" | "cancelled" | "expired";
  start: string;
  end: string;
  hold_expires_at: string | null;
  price_paise: number;
  therapist: string;
  client: string;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json" },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? "Something went wrong. Please try again.");
  return body as T;
}

export const api = {
  clients: () => call<Person[]>("/clients/"),
  therapists: () => call<Person[]>("/therapists/"),
  slots: (therapistId: number, startDate: string, endDate: string, tz: string) =>
    call<{ slots: Slot[] }>(
      `/therapists/${therapistId}/slots/?${new URLSearchParams({ start_date: startDate, end_date: endDate, tz })}`,
    ),
  hold: (clientId: number, therapistId: number, start: string) =>
    call<Booking>("/bookings/hold/", {
      method: "POST",
      body: JSON.stringify({ client_id: clientId, therapist_id: therapistId, start }),
    }),
  booking: (bookingId: number) => call<Booking>(`/bookings/${bookingId}/`),
  release: (bookingId: number) => call<Booking>(`/bookings/${bookingId}/release/`, { method: "POST" }),
  pay: (bookingId: number) => call<PaymentOrder>(`/bookings/${bookingId}/pay/`, { method: "POST" }),
};