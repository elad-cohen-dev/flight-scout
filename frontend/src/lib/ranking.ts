// How a run's deals are ordered: confirmed fares first, unconfirmed estimates after.
import { comparable, durationMinutes } from "./format";
import type { StoredDeal } from "../types";

export type Sort = "price" | "duration" | "depart";

/** Prices nobody confirmed for a bookable itinerary: Google Travel Explore's
 *  cheapest-dates price and the Aviasales cache. They never outrank a fare
 *  that was confirmed (Google Flights live, or what the web agent checked). */
export function isUnconfirmed(deal: Pick<StoredDeal, "source">): boolean {
  return deal.source === "google_explore" || deal.source === "aviasales";
}

const cmp = (d: StoredDeal) => comparable(d).price;

export function sortDeals(deals: StoredDeal[], sort: Sort): StoredDeal[] {
  const list = [...deals];
  if (sort === "price") list.sort((a, b) => cmp(a) - cmp(b));
  if (sort === "duration")
    list.sort((a, b) => (durationMinutes(a.duration) ?? 1e9) - (durationMinutes(b.duration) ?? 1e9) || cmp(a) - cmp(b));
  if (sort === "depart")
    list.sort((a, b) => `${a.depart_date} ${a.depart_time}`.localeCompare(`${b.depart_date} ${b.depart_time}`) || cmp(a) - cmp(b));
  return list;
}

export interface RankedDeals {
  confirmed: StoredDeal[]; // in `sort` order
  estimates: StoredDeal[]; // unconfirmed, in `sort` order, shown after (grouped when there are confirmed ones)
  cheapestId: number | null; // the "Cheapest" ribbon: the cheapest CONFIRMED fare when sorted by price
}

export function rankDeals(deals: StoredDeal[], sort: Sort): RankedDeals {
  const sorted = sortDeals(deals, sort);
  const confirmed = sorted.filter((d) => !isUnconfirmed(d));
  const estimates = sorted.filter(isUnconfirmed);
  const cheapestId = sort === "price" && deals.length > 1 && confirmed.length ? confirmed[0].id : null;
  return { confirmed, estimates, cheapestId };
}
