export const CURRENCIES = ["USD", "EUR", "GBP", "ILS"] as const;
const CURRENCY_SYMBOLS: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", ILS: "₪" };

export function currencySymbol(currency: string): string {
  return CURRENCY_SYMBOLS[currency] ?? currency;
}

export function formatPrice(price: number, currency: string): string {
  const symbol = CURRENCY_SYMBOLS[currency];
  const amount = price.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return symbol ? `${symbol}${amount}` : `${amount} ${currency}`;
}

/** A deal's price for ranking: the price with the requested bags (a Wizz fare
 *  without its checked bags isn't cheaper than an El Al one with them), and
 *  since sources quote their own currency ($600 vs ₪2,123), in the run's
 *  preferred currency when the backend could convert it, else the raw figure.
 *  Pre-3.0 servers send no compare_total: the bare fare, as before. */
export function comparable(d: {
  price: number;
  currency: string;
  compare_price?: number | null;
  compare_total?: number | null;
  compare_currency?: string;
  bags?: { total: number } | null;
}): {
  price: number;
  currency: string;
} {
  const converted = d.compare_total ?? d.compare_price;
  return converted != null && d.compare_currency
    ? { price: converted, currency: d.compare_currency }
    : { price: d.bags?.total ?? d.price, currency: d.currency };
}

export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = (now - new Date(iso).getTime()) / 1000;
  if (seconds < 90) return "just now";
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  const days = Math.round(hours / 24);
  if (days < 60) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** "2026-10-12" -> "Sun, Oct 12"; free-text dates ("mid October") pass through. */
export function formatTripDate(value: string): string {
  if (!ISO_DATE.test(value)) return value;
  const d = new Date(`${value}T00:00:00`);
  if (Number.isNaN(d.getTime())) return value;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Nights between two ISO dates, or null when either isn't an exact date. */
export function nightsBetween(depart: string, ret: string): number | null {
  if (!ISO_DATE.test(depart) || !ISO_DATE.test(ret)) return null;
  const n = Math.round((new Date(`${ret}T00:00:00`).getTime() - new Date(`${depart}T00:00:00`).getTime()) / 86_400_000);
  return n > 0 ? n : null;
}

/** "5h 20m" / "5h" / "320m" -> minutes; null when unparseable. */
export function durationMinutes(duration: string): number | null {
  const h = duration.match(/(\d+)\s*h/i);
  const m = duration.match(/(\d+)\s*m/i);
  if (!h && !m) return null;
  return (h ? Number(h[1]) * 60 : 0) + (m ? Number(m[1]) : 0);
}

export function stopsLabel(stops: number): string {
  return stops === 0 ? "Direct" : `${stops} stop${stops > 1 ? "s" : ""}`;
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
