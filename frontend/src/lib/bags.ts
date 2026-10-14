// "Real price with bags": wording for a deal's price with the requested bags
// (backend app/bags.py → StoredDeal.bags), the checkout preview, freshness and
// Recheck results. Google's fares exclude checked bags (and low-cost carriers'
// bag fees entirely), so the headline is fare + bags whenever bags were asked.
import { formatPrice, timeAgo } from "./format";
import { priceRange } from "./watches";
import type { BagLine, BagPricing, RecheckResult, StoredDeal } from "../types";

/** True when the deal's price has bag fees added on top of the fare (not
 *  when no bags were asked, the source quoted them itself, or the fare
 *  already includes every bag). Then the headline is the price with bags. */
export function bagsAdded(b: BagPricing | null | undefined): b is BagPricing {
  return !!b && b.basis !== "none" && b.basis !== "as_quoted" && b.total_max > b.fare;
}

/** The web agent quoted a price that already claims to include the bags. */
export function asQuoted(b: BagPricing | null | undefined): boolean {
  return b?.basis === "as_quoted";
}

const NAMES: Record<BagLine["kind"], [string, string]> = {
  trolley: ["trolley", "trolleys"],
  checked: ["checked bag", "checked bags"],
};

function bagNoun(kind: BagLine["kind"], n: number): string {
  return `${n} ${NAMES[kind][n === 1 ? 0 : 1]}`;
}

/** "2 checked bags", "2 trolleys + 2 checked bags". */
export function bagCountWords(b: BagPricing): string {
  return b.lines.map((l) => bagNoun(l.kind, l.count)).join(" + ");
}

/** A breakdown row's name: "2 checked bags × 2 ways" / "1 trolley, one way". */
export function lineLabel(l: BagLine): string {
  return `${bagNoun(l.kind, l.count)}${l.directions > 1 ? ` × ${l.directions} ways` : ", one way"}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10" -> "Oct 2026" ("" when unknown). */
export function asOfLabel(asOf: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(asOf || "");
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : "";
}

/** Where a bag fee came from, in plain words. */
export function basisLabel(l: BagLine, asOf: string): string {
  switch (l.basis) {
    case "google":
      return "Google added it";
    case "seller":
      return "seller's fee";
    case "included":
      return "included in fare";
    case "table": {
      const when = asOfLabel(asOf);
      return when ? `fee table, ${when}` : "fee table";
    }
    default:
      return "typical range — airline not in table";
  }
}

/** A line's fee: "₪180–520", "₪240", "free". */
export function lineAmount(l: BagLine, currency: string): string {
  if (l.high <= 0) return l.basis === "google" ? "in fare" : "free";
  return Math.round(l.low) === Math.round(l.high) ? formatPrice(l.high, currency) : priceRange(l.low, l.high, currency);
}

/** The total's range for the breakdown: "₪2,700–3,250" (a single price when exact). */
export function totalRange(b: BagPricing): string {
  return Math.round(b.total_min) === Math.round(b.total_max)
    ? formatPrice(b.total, b.currency)
    : priceRange(b.total_min, b.total_max, b.currency);
}

/** "≈" when a bag fee is a range, "" when every fee is exact. */
export function approx(b: BagPricing): string {
  return Math.round(b.total_min) === Math.round(b.total_max) ? "" : "≈ ";
}

/** What the seller's site will show first, next to the Book buttons:
 *  "Wizz Air shows about ₪2,140 — bags are added during checkout, ≈ ₪2,980 total". */
export function checkoutPreview(deal: Pick<StoredDeal, "airline" | "bags">): string | null {
  const b = deal.bags;
  if (!bagsAdded(b) || !b.added_at_checkout) return null;
  const who = deal.airline || "The airline";
  return `${who} shows about ${formatPrice(b.fare, b.currency)} — bags are added during checkout, ${approx(b)}${formatPrice(b.total, b.currency)} total`;
}

/** A seller without a stated bag fee: its price + the deal's estimated bag
 *  fees (same currency only), for "≈ ₪2,980 incl. bags (fee-table estimate)". */
export function sellerEstimate(sellerPrice: number | null, currency: string, b: BagPricing | null | undefined): number | null {
  if (sellerPrice == null || !bagsAdded(b) || b.currency !== currency) return null;
  return sellerPrice + (b.total - b.fare);
}

/** Short name of an estimate's source for labels: "fee-table estimate". */
export function estimateSource(b: BagPricing): string {
  return b.basis === "typical" ? "typical bag fees" : b.basis === "seller" ? "seller's fees" : "fee-table estimate";
}

/** "checked 3h ago": when the source saw the price (seen_at), else when we stored it. */
export function freshness(deal: Pick<StoredDeal, "seen_at" | "found_at">, now = Date.now()): string {
  const at = deal.seen_at || deal.found_at;
  if (!at || Number.isNaN(new Date(at).getTime())) return "";
  return `checked ${timeAgo(at, now)}`;
}

/** A Recheck's outcome: "↓ ₪120 since 3h ago" / "↑ ₪40 since 2d ago" / "Same price" / "No longer available". */
export function recheckText(r: Pick<RecheckResult, "status" | "delta" | "currency" | "old_seen_at">, now = Date.now()): string {
  if (r.status === "gone" || r.delta == null) return "No longer available";
  if (r.status === "same") return "Same price";
  const since = r.old_seen_at ? ` since ${freshness({ seen_at: r.old_seen_at, found_at: "" }, now).replace(/^checked /, "")}` : "";
  return `${r.delta < 0 ? "↓" : "↑"} ${formatPrice(Math.abs(r.delta), r.currency)}${since}`;
}
