// Opens Razorpay Checkout (their payment popup) for an order our server created.

import type { PaymentOrder } from "./api";

type RazorpayOptions = {
  key: string;
  order_id: string;
  amount: number;
  currency: string;
  name: string;
  description: string;
  handler: () => void;
  modal: { ondismiss: () => void };
};

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayOptions) => { open: () => void };
  }
}

function loadCheckoutScript(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Couldn't load Razorpay. Check your internet connection."));
    document.body.appendChild(script);
  });
}

/** Resolves "paid" when Razorpay reports success, "closed" if the user closes the popup.
 *  "paid" only means the browser THINKS it worked. The server waits for the webhook. */
export async function openCheckout(order: PaymentOrder, description: string): Promise<"paid" | "closed"> {
  await loadCheckoutScript();
  return new Promise((resolve) => {
    const checkout = new window.Razorpay!({
      key: order.key_id,
      order_id: order.order_id,
      amount: order.amount,
      currency: order.currency,
      name: "Care Desk",
      description,
      handler: () => resolve("paid"),
      modal: { ondismiss: () => resolve("closed") },
    });
    checkout.open();
  });
}