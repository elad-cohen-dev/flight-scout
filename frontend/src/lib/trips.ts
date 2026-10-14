// Trips (3.0): a family trip plan linking searches, watches and shortlisted deals.
// Types mirror backend/app/models.py (Trip*); helpers are pure so they're testable.
import type { Passengers, StoredDeal, Watch } from "../types";
import { formatPrice, shortDate } from "./format";
import { parseSearchQuery, passengersLabel } from "./search";

export type TripStatus = "planning" | "booked" | "done";
export type TripItemKind = "search" | "watch" | "deal";

export interface BookedFlight {
  deal_id: number | null;
  airline: string;
  flight_numbers: string;
  depart_date: string;
  return_date: string;
  price: number | null; // paid, whole party
  currency: string;
  booking_ref: string;
  notes: string;
  booked_at?: string | null;
}

/** The editable plan (backend TripCreate). */
export interface TripFields {
  name: string;
  notes: string;
  origin: string;
  destination: string;
  dates_hint: string;
  depart_from: string | null; // YYYY-MM-DD
  depart_to: string | null;
  passengers: Passengers;
  currency: string;
  status: TripStatus;
}

export interface TripBest {
  price: number;
  currency: string;
  kind: TripItemKind;
  label: string;
  deal_id: number | null;
}

export interface Trip extends TripFields {
  id: number;
  created_at: string;
  updated_at: string;
  booked: BookedFlight | null;
  items: number;
  best: TripBest | null; // cheapest price with bags across its items, in the trip currency
}

export interface TripItem {
  id: number;
  trip_id: number;
  kind: TripItemKind;
  ref_id: number | null;
  search_query: string;
  note: string;
  pinned: boolean;
  created_at: string;
}

export interface TripItemCreate {
  kind: TripItemKind;
  ref_id?: number;
  search_query?: string;
  note?: string;
  pinned?: boolean;
}

export interface TripDetail extends Trip {
  searches: { item: TripItem; route: string; runs: number; latest_run_at: string | null; deals: StoredDeal[] }[];
  watches: { item: TripItem; watch: Watch | null }[];
  deals: { item: TripItem; deal: StoredDeal | null }[]; // the shortlist, pinned first
  booked_deal: StoredDeal | null;
}

/** The price-with-bags fields every deal carries since 3.0 (backend BagPricing).
 *  Declared here as optional so this file works whatever types.ts says. */
export interface BagsInfo {
  basis: string; // none | as_quoted | included | table | typical | seller
  fare: number;
  total: number;
  total_min: number;
  total_max: number;
  currency: string;
  added_at_checkout?: boolean;
}
export type DealWithBags = Pick<StoredDeal, "price" | "currency" | "compare_price" | "compare_currency"> & {
  bags?: BagsInfo | null;
  compare_total?: number | null;
};

export const STATUS_LABELS: Record<TripStatus, string> = { planning: "Planning", booked: "Booked", done: "Done" };

/** What the family pays: the price with the requested bags, in the run's
 *  currency (compare_total ?? compare_price), else the raw price with bags. */
export function dealTotal(d: DealWithBags): { price: number; currency: string } {
  if (d.compare_total != null && d.compare_currency) return { price: d.compare_total, currency: d.compare_currency };
  if (d.compare_price != null && d.compare_currency) return { price: d.compare_price, currency: d.compare_currency };
  return { price: d.bags?.total ?? d.price, currency: d.currency };
}

const BASIS_WORDS: Record<string, string> = {
  google: "in the fare",
  included: "included in the fare",
  table: "airline fee table",
  typical: "typical fee, airline unknown",
  seller: "seller's fee",
  as_quoted: "as quoted by the source",
};

/** The bags column of the compare table: what they add and how sure that is.
 *  null when the search asked for no bags. */
export function bagsSummary(d: DealWithBags): { text: string; tip: string } | null {
  const b = d.bags;
  if (!b || b.basis === "none") return null;
  const tip = BASIS_WORDS[b.basis] ?? b.basis;
  if (b.basis === "as_quoted" || b.basis === "included") return { text: "Included", tip };
  const lo = Math.max(0, b.total_min - b.fare);
  const hi = Math.max(0, b.total_max - b.fare);
  if (hi < 0.5) return { text: "Included", tip };
  const range = Math.round(lo) === Math.round(hi) ? formatPrice(hi, b.currency) : `${formatPrice(lo, b.currency)}–${Math.round(hi).toLocaleString()}`;
  return { text: `+${range}`, tip: `${tip}${b.added_at_checkout ? " · added at checkout" : ""}` };
}

export type Freshness = "fresh" | "aging" | "stale";

/** How old a price is (the source's seen_at, else when it was stored):
 *  under a day is fresh, under 3 days aging, older is stale — fares move. */
export function freshness(d: Pick<StoredDeal, "found_at" | "seen_at">, now = Date.now()): Freshness {
  const t = Date.parse(d.seen_at || d.found_at);
  const hours = Number.isNaN(t) ? Infinity : (now - t) / 3_600_000;
  return hours < 24 ? "fresh" : hours < 72 ? "aging" : "stale";
}

/** The cheapest deal (by price with bags) among those in the most common
 *  currency — prices in another currency can't be ranked against them. */
export function cheapestId(deals: (DealWithBags & { id: number })[]): number | null {
  if (deals.length < 2) return null;
  const counts = new Map<string, number>();
  for (const d of deals) counts.set(dealTotal(d).currency, (counts.get(dealTotal(d).currency) ?? 0) + 1);
  const main = [...counts].sort((a, b) => b[1] - a[1])[0][0];
  const same = deals.filter((d) => dealTotal(d).currency === main);
  return same.reduce((best, d) => (dealTotal(d).price < dealTotal(best).price ? d : best)).id;
}

/** "TLV → Rome", or "" when the trip has no route yet. */
export function tripRoute(t: Pick<TripFields, "origin" | "destination">): string {
  return [t.origin, t.destination].filter((s) => s.trim()).join(" → ");
}

/** The trip's window: its free text, else the exact dates. */
export function tripDates(t: Pick<TripFields, "dates_hint" | "depart_from" | "depart_to">): string {
  if (t.dates_hint.trim()) return t.dates_hint.trim();
  const day = (iso: string) => shortDate(`${iso}T00:00:00`);
  if (t.depart_from && t.depart_to) return t.depart_from === t.depart_to ? day(t.depart_from) : `${day(t.depart_from)} – ${day(t.depart_to)}`;
  if (t.depart_from) return `from ${day(t.depart_from)}`;
  if (t.depart_to) return `by ${day(t.depart_to)}`;
  return "";
}

export function partyLabel(p: Passengers): string {
  return passengersLabel(p);
}

/** Planning trips first, then booked, then done; most recently changed first within each. */
export function sortTrips<T extends Pick<Trip, "status" | "updated_at">>(trips: T[]): T[] {
  const order: Record<TripStatus, number> = { planning: 0, booked: 1, done: 2 };
  return [...trips].sort((a, b) => order[a.status] - order[b.status] || b.updated_at.localeCompare(a.updated_at));
}

/** A search's identity for a trip: its label without a watch's "[watch #N]" tag. */
export function searchRef(searchQuery: string): string {
  return searchQuery.replace(/\s*\[watch #\d+\]\s*$/, "").trim();
}

export const EMPTY_TRIP: TripFields = {
  name: "",
  notes: "",
  origin: "",
  destination: "",
  dates_hint: "",
  depart_from: null,
  depart_to: null,
  passengers: { adults: 2, children: 0, infants: 0 },
  currency: "ILS",
  status: "planning",
};

const MONTH = /^(\d{4})-(\d{2})-\d{2}$/;

function monthName(iso: string): string {
  const m = iso.match(MONTH);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "";
}

/** "TLV" / "Rome [FCO]" / "Rome" -> the city part, for a trip name. */
function place(s: string): string {
  return s.replace(/\s*\[[^\]]*\]/, "").trim();
}

/** A new trip pre-filled from what "Add to trip ▸ New trip…" was pressed on. */
export function tripFromDeal(d: StoredDeal): Partial<TripFields> {
  const parsed = parseSearchQuery(d.search_query);
  const origin = place(parsed?.origin ?? d.origin);
  const destination = place(parsed?.destination ?? d.destination);
  const when = monthName(d.depart_date);
  return {
    name: [destination, when].filter(Boolean).join(" · "),
    origin,
    destination,
    dates_hint: parsed?.dates_hint ?? "",
    passengers: d.search_params?.passengers ?? EMPTY_TRIP.passengers,
    currency: d.compare_currency || d.currency,
  };
}

export function tripFromWatch(w: Watch): Partial<TripFields> {
  return {
    name: place(w.destination),
    origin: place(w.origin),
    destination: place(w.destination),
    dates_hint: w.dates_hint,
    depart_from: w.depart_from,
    depart_to: w.depart_to,
    passengers: w.passengers,
    currency: w.currency,
  };
}

/** The booked form pre-filled from a shortlisted deal: the price with bags is what was paid. */
export function bookedFromDeal(d: StoredDeal & DealWithBags): BookedFlight {
  return {
    deal_id: d.id,
    airline: d.airline,
    flight_numbers: d.flight_numbers,
    depart_date: d.depart_date,
    return_date: d.return_date,
    price: Math.round(d.bags?.total ?? d.price),
    currency: d.bags?.currency ?? d.currency,
    booking_ref: "",
    notes: "",
  };
}

export const EMPTY_BOOKED: BookedFlight = {
  deal_id: null,
  airline: "",
  flight_numbers: "",
  depart_date: "",
  return_date: "",
  price: null,
  currency: "ILS",
  booking_ref: "",
  notes: "",
};

/** One line for a trip card's price: what was paid once booked, else the best with bags. */
export function tripPriceLine(t: Pick<Trip, "booked" | "best">): { label: string; price: string } | null {
  if (t.booked?.price != null) return { label: "Paid", price: formatPrice(t.booked.price, t.booked.currency) };
  if (t.best) return { label: "Best with bags", price: formatPrice(t.best.price, t.best.currency) };
  return null;
}

/** The trip's own hash route: "#trips/12" opens trip 12, "#trips" the list. */
export function tripIdFromHash(hash: string): number | null {
  const m = hash.match(/^#\/?trips\/(\d+)/);
  return m ? Number(m[1]) : null;
}
