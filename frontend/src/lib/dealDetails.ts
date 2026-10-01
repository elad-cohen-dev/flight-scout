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
