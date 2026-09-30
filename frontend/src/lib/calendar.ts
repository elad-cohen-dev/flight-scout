import type { CabinClass, HistoryPoint, SearchRequest, StoredDeal, TripType } from "../types";
import { comparable, nightsBetween } from "./format";
import { CABIN_LABELS, dealCriteria, rerunRequest, totalPassengers } from "./search";

/* Price calendar: best fare per departure date, per requested route.
 *
 * Best-price / staleness rule (one place, tested in calendar.test.ts):
 *  - A sighting is STALE when it was found STALE_DAYS (30) or more days ago.
 *  - For each departure date, the best price is the cheapest FRESH sighting;
 *    stale sightings only fill a day that has no fresh one (and the cell is
 *    then drawn faded). A fresher price wins even when an older sighting was
 *    cheaper — fares move, and a month-old low is not bookable evidence. The
 *    cheaper stale figure is kept as `staleLow` so the detail can mention it.
 *  - Ties on price go to the most recent sighting.
 *  - The same fare re-found by several runs counts once (latest sighting kept).
 *  - Prices are compared via comparable() and only within the route's
 *    dominant comparison currency; other-currency sightings are counted, not
 *    ranked, since we can't convert client-side.
 *  - "Cheapest day" marks go to the lowest best price among fresh days when
 *    any exist, else among all days — so a stale low never outranks live data.
 */
export const STALE_DAYS = 30;
const DAY_MS = 86_400_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export const isIsoDate = (s: string): boolean => ISO_DATE.test(s);

/** 'TLV -> London (anytime in October) [watch #4]' -> 'TLV -> London' — mirrors the backend's _route_of. */
export function routeKey(searchQuery: string): string {
  return searchQuery.split(" (")[0].replace(/\s*\[[^\]]*\]/g, "").trim();
}

/** Route id a deal belongs to: the backend's canonical route_key ("TLV>PRG"),
 *  falling back to the label's route for payloads without one. */
export function dealRoute(d: StoredDeal): string {
  return d.route_key || routeKey(d.search_query);
}

/** Local calendar date of a Date as YYYY-MM-DD. */
export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Calendar arithmetic on YYYY-MM-DD strings (UTC-based, so DST can't shift a day). */
export function addDays(iso: string, n: number): string {
  const t = Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) + n * DAY_MS;
  return new Date(t).toISOString().slice(0, 10);
}

export function addMonths(ym: string, n: number): string {
  const idx = +ym.slice(0, 4) * 12 + (+ym.slice(5, 7) - 1) + n;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

export function monthEnd(ym: string): string {
  return addDays(`${addMonths(ym, 1)}-01`, -1);
}

/** Weeks (Monday first) of a "YYYY-MM" month; null pads days outside it. */
export function monthGrid(ym: string): (string | null)[][] {
  const first = `${ym}-01`;
  const last = monthEnd(ym);
  const lead = (new Date(`${first}T00:00:00Z`).getUTCDay() + 6) % 7;
  const cells: (string | null)[] = Array(lead).fill(null);
  for (let d = first; d <= last; d = addDays(d, 1)) cells.push(d);
  while (cells.length % 7) cells.push(null);
  const weeks: (string | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

/** A search's fare "kind": prices are only comparable within one kind
 *  (a 2-traveller business total isn't cheaper or dearer than a solo economy fare). */
export interface Variant {
  key: string;
  label: string;
  party: number;
  cabin: CabinClass;
  trip: TripType;
}

export function variantOf(d: StoredDeal): Variant {
  const c = dealCriteria(d);
  const party = c.passengers ? totalPassengers(c.passengers) : 1;
  const cabin = c.cabin_class ?? "economy";
  const trip: TripType = d.search_params?.trip_type ?? (d.return_date ? "round_trip" : "one_way");
  const label = [party === 1 ? "1 traveller" : `${party} travellers`, CABIN_LABELS[cabin], trip === "one_way" ? "One way" : "Round trip"].join(" · ");
  return { key: `${party}|${cabin}|${trip}`, label, party, cabin, trip };
}

export interface CalendarFilter {
  variant: string | null; // Variant.key; null = the latest search's kind
  directOnly: boolean;
}

export interface Sighting {
  deal: StoredDeal;
  price: number; // comparable price
  foundAt: string;
  ageDays: number;
  stale: boolean;
}

export interface DayCell {
  date: string;
  best: Sighting;
  fares: Sighting[]; // unique fares, fresh first, then cheapest
  stale: boolean; // best comes from a stale sighting
  staleLow: Sighting | null; // an older, cheaper sighting the fresh best beat
  returnDate: string;
  nights: number | null;
}

export type Tier = "low" | "mid" | "high";

export interface RouteCalendar {
  route: string;
  currency: string;
  days: Map<string, DayCell>; // today and later only
  months: string[]; // "YYYY-MM" with at least one upcoming day
  min: number;
  max: number;
  cheapestDates: string[];
  undated: number; // matching deals without an exact departure date
  undatedSample: string; // e.g. "mid October", for the note under the grid
  past: number; // matching deals whose departure already passed
  otherCurrency: number; // matching dated deals in a different comparison currency
  variants: Variant[]; // kinds present on the route, latest first
  variant: Variant | null; // the kind shown
  latest: StoredDeal | null; // most recent sighting of the shown kind
}

function fareKey(d: StoredDeal): string {
  return [d.airline, d.origin_airport || d.origin, d.destination_airport || d.destination, d.depart_date, d.return_date, d.stops, Math.round(comparable(d).price)]
    .map((v) => String(v).toLowerCase())
    .join("|");
}

/** Newer sighting first; used to break price ties and to dedupe re-found fares. */
const newer = (a: Sighting, b: Sighting) => b.foundAt.localeCompare(a.foundAt);

/** The rule documented at the top of the file, for one day's sightings. */
export function pickBest(sightings: Sighting[]): { best: Sighting; staleLow: Sighting | null } {
  const byPrice = (a: Sighting, b: Sighting) => a.price - b.price || newer(a, b);
  const fresh = sightings.filter((s) => !s.stale).sort(byPrice);
  const stale = sightings.filter((s) => s.stale).sort(byPrice);
  const best = fresh[0] ?? stale[0];
  const staleLow = fresh.length && stale.length && stale[0].price < best.price ? stale[0] : null;
  return { best, staleLow };
}

export function tierOf(price: number, min: number, max: number): Tier {
  if (max <= min) return "low";
  const t = (price - min) / (max - min);
  return t < 1 / 3 ? "low" : t < 2 / 3 ? "mid" : "high";
}

export function buildCalendar(allDeals: StoredDeal[], route: string, filter: CalendarFilter, now = new Date()): RouteCalendar {
  const today = isoDay(now);
  const routeDeals = allDeals.filter((d) => dealRoute(d) === route).sort((a, b) => b.found_at.localeCompare(a.found_at));

  const variants: Variant[] = [];
  for (const d of routeDeals) {
    const v = variantOf(d);
    if (!variants.some((x) => x.key === v.key)) variants.push(v);
  }
  const variant = variants.find((v) => v.key === filter.variant) ?? variants[0] ?? null;
  const matching = routeDeals.filter((d) => variantOf(d).key === variant?.key && (!filter.directOnly || d.stops === 0));
  const latest = matching[0] ?? null;

  let undated = 0;
  let undatedSample = "";
  let past = 0;
  const dated: StoredDeal[] = [];
  for (const d of matching) {
    if (!isIsoDate(d.depart_date)) {
      undated++;
      undatedSample ||= d.depart_date.trim();
    }
    else if (d.depart_date < today) past++;
    else dated.push(d);
  }

  // The route's comparison currency: the most common one among its dated deals.
  const counts = new Map<string, number>();
  for (const d of dated) counts.set(comparable(d).currency, (counts.get(comparable(d).currency) ?? 0) + 1);
  const currency = [...counts].sort((a, b) => b[1] - a[1])[0]?.[0] ?? latest?.compare_currency ?? "USD";

  const byDate = new Map<string, Map<string, Sighting>>();
  let otherCurrency = 0;
  for (const d of dated) {
    const c = comparable(d);
    if (c.currency !== currency) {
      otherCurrency++;
      continue;
    }
    const ageDays = (now.getTime() - new Date(d.found_at).getTime()) / DAY_MS;
    const s: Sighting = { deal: d, price: c.price, foundAt: d.found_at, ageDays, stale: ageDays >= STALE_DAYS };
    const fares = byDate.get(d.depart_date) ?? new Map<string, Sighting>();
    const key = fareKey(d);
    const prev = fares.get(key);
    if (!prev || newer(s, prev) < 0) fares.set(key, s);
    byDate.set(d.depart_date, fares);
  }

  const days = new Map<string, DayCell>();
  for (const [date, fares] of [...byDate].sort((a, b) => a[0].localeCompare(b[0]))) {
    const list = [...fares.values()];
    const { best, staleLow } = pickBest(list);
    list.sort((a, b) => Number(a.stale) - Number(b.stale) || a.price - b.price || newer(a, b));
    days.set(date, {
      date,
      best,
      fares: list,
      stale: best.stale,
      staleLow,
      returnDate: isIsoDate(best.deal.return_date) ? best.deal.return_date : "",
      nights: nightsBetween(best.deal.depart_date, best.deal.return_date),
    });
  }

  const cells = [...days.values()];
  const prices = cells.map((c) => c.best.price);
  const pool = cells.some((c) => !c.stale) ? cells.filter((c) => !c.stale) : cells;
  const low = pool.length ? Math.min(...pool.map((c) => c.best.price)) : 0;
  return {
    route: routeDeals[0] ? routeKey(routeDeals[0].search_query) : route, // display label
    currency,
    days,
    months: [...new Set(cells.map((c) => c.date.slice(0, 7)))],
    min: prices.length ? Math.min(...prices) : 0,
    max: prices.length ? Math.max(...prices) : 0,
    cheapestDates: pool.filter((c) => c.best.price === low).map((c) => c.date),
    undated,
    undatedSample,
    past,
    otherCurrency,
    variants,
    variant,
    latest,
  };
}

/** Search-form criteria for a depart window on this route: the latest search's
 *  criteria with the dates replaced (never earlier than today). */
export function windowRequest(latest: StoredDeal, from: string, to: string, now = new Date()): SearchRequest | null {
  const base = rerunRequest(latest);
  if (!base) return null;
  const today = isoDay(now);
  const start = from < today ? today : from;
  return { ...base, depart_from: start, depart_to: to < start ? start : to };
}

/** "Search around this date": ±2 days. */
export function aroundDateRequest(latest: StoredDeal, date: string, now = new Date()): SearchRequest | null {
  return windowRequest(latest, addDays(date, -2), addDays(date, 2), now);
}

/** "Fill this month": the whole visible month (from today if it's the current one). */
export function monthRequest(latest: StoredDeal, ym: string, now = new Date()): SearchRequest | null {
  return windowRequest(latest, `${ym}-01`, monthEnd(ym), now);
}


/** Kinds of fare (party · cabin · trip type) searched on a route, latest first. */
export function routeVariants(allDeals: StoredDeal[], route: string): Variant[] {
  const variants: Variant[] = [];
  const routeDeals = allDeals.filter((d) => dealRoute(d) === route).sort((a, b) => b.found_at.localeCompare(a.found_at));
  for (const d of routeDeals) {
    const v = variantOf(d);
    if (!variants.some((x) => x.key === v.key)) variants.push(v);
  }
  return variants;
}

/** Best comparable price per search run for one kind of fare on a route —
 *  the chart's points. Prices are party totals, so mixing a 2-traveller run
 *  with 1-traveller runs (what the backend's per-route history does) would
 *  make "since first run" meaningless. Runs are compared in the route's most
 *  common comparison currency; deals in any other currency are skipped. */
export function runPoints(allDeals: StoredDeal[], route: string, variantKey: string | null): { points: HistoryPoint[]; currency: string } {
  const deals = allDeals.filter((d) => dealRoute(d) === route && (variantKey == null || variantOf(d).key === variantKey));
  const counts = new Map<string, number>();
  for (const d of deals) counts.set(comparable(d).currency, (counts.get(comparable(d).currency) ?? 0) + 1);
  const currency = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "USD";
  const runs = new Map<string, { price: number; deals: number }>();
  for (const d of deals) {
    const c = comparable(d);
    if (c.currency !== currency) continue;
    const run = runs.get(d.found_at) ?? { price: Infinity, deals: 0 };
    run.price = Math.min(run.price, c.price);
    run.deals++;
    runs.set(d.found_at, run);
  }
  const points = [...runs.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([found_at, r]) => ({ found_at, price: r.price, deals: r.deals }));
  return { points, currency };
}
