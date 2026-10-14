// "Was it the same price?": after a Book click takes the user to a seller, the
// deal card asks — once they come back to the tab — whether the seller's total
// matched ours. The answers (POST /api/price-checks) become the accuracy line
// on the Trends tab. One pending click at a time, kept in memory and
// sessionStorage (a reload while on the seller's site keeps it).
import { formatPrice } from "./format";
import type { PriceCheckSummary } from "../types";

export interface PendingCheck {
  dealId: number;
  seller: string; // the clicked link's label
  shownTotal: number; // what the card showed (with bags when asked)
  currency: string;
  at: number; // ms
}

const KEY = "fs_pending_price_check";
const MIN_AWAY_MS = 4_000; // a quick tab flick isn't a visit to the seller
const MAX_AWAY_MS = 3 * 3600_000; // after this the answer isn't worth asking for

let pending: PendingCheck | null = null;

export function noteBookClick(p: Omit<PendingCheck, "at">, now = Date.now()): void {
  pending = { ...p, at: now };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(pending));
  } catch {
    // memory only
  }
}

function load(): PendingCheck | null {
  if (pending) return pending;
  try {
    const raw = sessionStorage.getItem(KEY);
    pending = raw ? (JSON.parse(raw) as PendingCheck) : null;
  } catch {
    pending = null;
  }
  return pending;
}

export function clearPending(): void {
  pending = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing stored
  }
}

/** Whether to ask now about `dealId`: its card's link was the last one
 *  clicked, the user was away long enough to have looked, and not for hours. */
export function shouldAsk(p: PendingCheck | null, dealId: number, now: number): boolean {
  if (!p || p.dealId !== dealId) return false;
  const away = now - p.at;
  return away >= MIN_AWAY_MS && away <= MAX_AWAY_MS;
}

/** On returning to the tab: the pending check for `dealId` to ask about (and
 *  forget), or null. */
export function takeFor(dealId: number, now = Date.now()): PendingCheck | null {
  const p = load();
  if (!shouldAsk(p, dealId, now)) {
    if (p && now - p.at > MAX_AWAY_MS) clearPending();
    return null;
  }
  clearPending();
  return p;
}

/** A typed total: "2,980" / "₪2980" / "2980.50" -> 2980.5; null when not a positive number. */
export function parseTotal(text: string): number | null {
  const n = Number(text.replace(/[^\d.]/g, ""));
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** The Trends tab's line: "Price checks: 7 of 9 matched; Wizz Air bags off by ~€15 (more at checkout)".
 *  The airline named is the one with the biggest typical difference. null before any check. */
export function accuracyLine(s: PriceCheckSummary | null | undefined): string | null {
  if (!s || s.checks === 0) return null;
  let text = `Price checks: ${s.same} of ${s.checks} matched`;
  const off = s.airlines
    .filter((a) => a.median_diff != null && Math.round(Math.abs(a.median_diff)) > 0)
    .sort((a, b) => Math.abs(b.median_diff!) - Math.abs(a.median_diff!))[0];
  if (off) {
    const diff = off.median_diff!;
    const what = off.bags_estimated ? `${off.airline} bags` : off.airline;
    text += `; ${what} off by ~${formatPrice(Math.abs(diff), off.currency)} (${diff > 0 ? "more" : "less"} at checkout)`;
  }
  return text;
}
