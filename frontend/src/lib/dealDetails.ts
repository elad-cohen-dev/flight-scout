// "Details & book direct": wording and choices for a deal's on-demand booking details.
import { formatPrice } from "./format";
import type { BagFee, DealDetails, Seller, SellerLink } from "../types";

/** Before asking the server: the most a fetch can use ("uses 1–2 live searches"). */
export function costHint(maxCost: number): string {
  return maxCost > 1 ? `uses 1–${maxCost} live searches` : "uses 1 live search";
}

/** The exact quote: "Uses 2 of your 223 live searches left". */
export function costLine(d: Pick<DealDetails, "cost" | "left">): string {
  if (d.cost === 0) return "Free — already looked up";
  return `Uses ${d.cost} of your ${d.left} live search${d.left === 1 ? "" : "es"} left`;
}

export function canFetch(d: Pick<DealDetails, "status" | "cost" | "left" | "message">): boolean {
  return d.status === "needs_fetch" && d.left >= d.cost && !d.message;
}

/** The cheapest seller that is the airline itself and books on one ticket. */
export function directSeller(d: DealDetails | null | undefined): { seller: Seller; link: SellerLink } | null {
  for (const seller of d?.sellers ?? []) {
    const link = seller.links.find((l) => l.part === "together" && l.url);
    if (seller.airline && link) return { seller, link };
  }
  return null;
}

function bagText(kind: string, bag: BagFee, currency: string): string | null {
  if (bag.fee === 0) return `${kind} free`;
  if (bag.fee != null) return `${kind} ${formatPrice(bag.fee, currency)}/traveller`;
  if (!bag.text) return null;
  // "1st checked bag: 42-60" -> "Checked bag 42-60"; other wordings as Google says them.
  const tail = bag.text.includes(":") ? bag.text.split(":").slice(1).join(":").trim() : "";
  return tail ? `${kind} ${tail}` : bag.text;
}

/** ["Carry-on free", "Checked bag $35/traveller"] for a seller. */
export function bagSummary(s: Seller, currency: string): string[] {
  return [bagText("Carry-on", s.carry_on, currency), bagText("Checked bag", s.checked, currency)].filter(
    (x): x is string => !!x,
  );
}

/** Button text for a seller's ticket(s): "Book with Smartwings", "Outbound ticket". */
export function linkLabel(s: Seller, link: SellerLink): string {
  if (link.part === "departing") return `Outbound: ${link.label}`;
  if (link.part === "returning") return `Return: ${link.label}`;
  return `Book with ${s.book_with}`;
}

/** The API's error detail ("409: {"detail":"Needs 2…"}" -> "Needs 2…"). */
export function errorText(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  const m = /^\d{3}: (.*)$/s.exec(text);
  if (m) {
    try {
      const detail = (JSON.parse(m[1]) as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
    } catch {
      // not JSON — fall through
    }
  }
  return text || "Something went wrong";
}

type Bag = "carry_on" | "checked";
const BAG_WORD: Record<Bag, string> = { carry_on: "trolley", checked: "20kg bag" };

/** "20kg bag", "trolley + 20kg bag". */
export function bagWords(bags: Bag[]): string {
  return bags.map((b) => BAG_WORD[b]).join(" + ");
}

/** The seller the card's primary button books: the direct airline, else the top-ranked seller. */
export function selectedSeller(d: DealDetails | null | undefined): Seller | null {
  if (d?.status !== "ready") return null;
  return directSeller(d)?.seller ?? d.sellers[0] ?? null;
}

/** The selected seller's exact total incl. every requested bag, or null. */
export function bagTotal(d: DealDetails | null | undefined): number | null {
  const s = selectedSeller(d);
  return s?.bags_verified && s.price_incl_bags != null ? s.price_incl_bags : null;
}

/** Whether a source's quoted price already includes a requested bag: Google
 *  Flights adds carry-on fees (its bags filter) but has no checked-bag filter;
 *  Aviasales prices never include bags; the web agent is told to add the fees. */
export function priceIncludesBag(source: string | undefined, bag: Bag): boolean {
  if (source === "aviasales") return false;
  if (source === "google_flights") return bag === "carry_on";
  return true;
}

export interface BagBadge {
  bag: Bag;
  label: string;
  tip: string;
  included: boolean;
}

/** Bag badges for a card: "incl." only when the shown price includes the bag;
 *  "not in price" until an exact bag-inclusive total is known; then nothing
 *  (the total under the price says it). */
export function bagBadges(
  source: string | undefined,
  criteria: { cabin_bag?: boolean; checked_bag?: boolean },
  details: DealDetails | null | undefined,
): BagBadge[] {
  const exact = bagTotal(details) != null;
  const out: BagBadge[] = [];
  for (const bag of ["carry_on", "checked"] as Bag[]) {
    if (!(bag === "carry_on" ? criteria.cabin_bag : criteria.checked_bag)) continue;
    const word = bag === "carry_on" ? "Trolley" : "20kg bag";
    const what = bag === "carry_on" ? "a 10kg cabin trolley" : "a 20kg+ checked bag";
    if (priceIncludesBag(source, bag)) {
      out.push({ bag, label: `${word} incl.`, tip: `Price includes ${what}`, included: true });
    } else if (!exact) {
      out.push({
        bag,
        label: `${word} not in price`,
        tip: `This fare excludes ${what}; add the airline's fee (Details & book direct shows it)`,
        included: false,
      });
    }
  }
  return out;
}

/** What to add at the seller's checkout, with the exact fees; null when a fee isn't exact. */
export function bagCheckoutNote(s: Seller | null, bags: Bag[], currency: string): string | null {
  if (!s || !bags.length) return null;
  const parts: string[] = [];
  for (const bag of bags) {
    const fee = (bag === "carry_on" ? s.carry_on : s.checked).fee;
    if (fee == null) return null;
    parts.push(
      fee === 0
        ? `${s.book_with} includes the ${BAG_WORD[bag]} free`
        : `Add the ${BAG_WORD[bag]} at ${s.book_with} checkout: ${formatPrice(fee, currency)} per traveller`,
    );
  }
  return parts.join("; ");
}
