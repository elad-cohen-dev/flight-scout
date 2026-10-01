// Explore mode: criteria, the free client-side filters, and turning a
// destination into a live search or a watch. Backend: app/explore.py.
import type {
  CabinClass,
  ExploreDestination,
  ExploreQuote,
  ExploreRegion,
  ExploreRequest,
  Passengers,
  SearchRequest,
  TravelDuration,
  TripType,
} from "../types";
import { currencySymbol, formatPrice } from "./format";
import { EXACT_DATES_HINT } from "./search";

export interface ExploreForm {
  origin: string;
  month: number; // 0 = any of the next six months
  travel_duration: TravelDuration;
  trip_type: TripType;
  direct_only: boolean;
  passengers: Passengers;
  cabin_class: CabinClass;
  cabin_bag: boolean;
  currency: string;
  // Free filters (applied to the same cached result):
  max_price: string;
  region: ExploreRegion | "";
}

export const DEFAULT_EXPLORE: ExploreForm = {
  origin: "TLV",
  month: 0,
  travel_duration: "week",
  trip_type: "round_trip",
  direct_only: false,
  passengers: { adults: 1, children: 0, infants: 0 },
  cabin_class: "economy",
  cabin_bag: false,
  currency: "USD",
  max_price: "",
  region: "",
};

export const REGION_LABELS: Record<ExploreRegion, string> = {
  europe: "Europe",
  caucasus: "Caucasus",
  middle_east: "Middle East",
  africa: "Africa",
  asia: "Asia",
  americas: "Americas",
  oceania: "Oceania",
};

export const DURATION_LABELS: Record<TravelDuration, string> = { weekend: "Weekend", week: "1 week", two_weeks: "2 weeks" };
// The trip lengths a watch on an explored destination accepts.
const DURATION_NIGHTS: Record<TravelDuration, [number, number]> = { weekend: [2, 3], week: [6, 8], two_weeks: [13, 15] };

const KEY = "fs_last_explore";

export function loadExplore(): ExploreForm {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_EXPLORE;
    const saved = JSON.parse(raw) as Partial<ExploreForm>;
    return { ...DEFAULT_EXPLORE, ...saved, passengers: { ...DEFAULT_EXPLORE.passengers, ...saved.passengers } };
  } catch {
    return DEFAULT_EXPLORE;
  }
}

export function saveExplore(form: ExploreForm): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(form));
  } catch {
    // remembering is best-effort
  }
}

/** The six months Google Explore answers, from this one: [{value: 10, label: "Oct"}, …]. */
export function monthOptions(today = new Date()): { value: number; label: string }[] {
  return Array.from({ length: 6 }, (_, i) => {
    const d = new Date(today.getFullYear(), today.getMonth() + i, 1);
    const sameYear = d.getFullYear() === today.getFullYear();
    return {
      value: d.getMonth() + 1,
      label: d.toLocaleDateString("en", { month: "short", ...(sameYear ? {} : { year: "2-digit" }) }),
    };
  });
}

/** A remembered month that has since dropped out of the six-month horizon becomes "any". */
export function validMonth(month: number, today = new Date()): number {
  return month && monthOptions(today).some((m) => m.value === month) ? month : 0;
}

/** What goes to the backend. Only the fields Google prices on — the price cap
 *  and region are filtered here, so changing them never costs a search. */
export function exploreRequest(f: ExploreForm, today = new Date()): ExploreRequest {
  return {
    origin: f.origin.trim() || "TLV",
    month: validMonth(f.month, today),
    travel_duration: f.travel_duration,
    trip_type: f.trip_type,
    max_stops: f.direct_only ? 0 : null,
    passengers: f.passengers,
    cabin_class: f.cabin_class,
    cabin_bag: f.cabin_bag,
    currency: f.currency,
  };
}

/** Stable id of a destination (Google's city kgmid). */
export const destinationKey = (d: Pick<ExploreDestination, "destination_id" | "search_destination">) => d.destination_id || d.search_destination;

export function filterDestinations(list: ExploreDestination[], f: Pick<ExploreForm, "max_price" | "region">): ExploreDestination[] {
  const cap = Number(f.max_price);
  return list.filter((d) => (!f.region || d.region === f.region) && (!(cap > 0) || d.price <= cap));
}

/** Regions present in a result, most destinations first, with counts. */
export function regionCounts(list: ExploreDestination[]): { region: ExploreRegion; count: number }[] {
  const counts = new Map<ExploreRegion, number>();
  for (const d of list) if (d.region) counts.set(d.region, (counts.get(d.region) ?? 0) + 1);
  return [...counts].map(([region, count]) => ({ region, count })).sort((a, b) => b.count - a.count);
}

/** "1 live search · 225 left" / "Cached — free · 225 left" / null when live search is off. */
export function costLine(q: ExploreQuote | null): string | null {
  if (!q || !q.configured) return null;
  if (q.cached) return `Cached — free · ${q.left} left this month`;
  if (q.left <= 0) return `No live searches left this month (budget ${q.budget})`;
  return `1 live search · ${q.left} left this month`;
}

/** Compact marker label: "$89", "€1.2k". */
export function markerLabel(price: number, currency: string): string {
  const sym = currencySymbol(currency);
  const amount =
    price >= 10_000 ? `${Math.round(price / 1000)}k` : price >= 1000 ? `${(price / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(Math.round(price));
  return sym === currency ? `${amount} ${currency}` : `${sym}${amount}`;
}

/** Screen-reader / list summary: "Paphos, Cyprus — $89, direct, Oct 23 – Oct 30". */
export function describeDestination(d: ExploreDestination): string {
  const when = d.return_date ? `${d.depart_date} to ${d.return_date}` : d.depart_date;
  return `${d.city}, ${d.country} — ${formatPrice(d.price, d.currency)}, ${d.stops === 0 ? "direct" : `${d.stops} stop${d.stops > 1 ? "s" : ""}`}, ${when}`;
}

/** "Check this fare live": a normal search for exactly the dates Explore found (1 search). */
export function liveSearchRequest(d: ExploreDestination, f: ExploreForm): SearchRequest {
  const roundTrip = f.trip_type === "round_trip" && !!d.return_date;
  const nights = roundTrip ? d.nights : null;
  return {
    origin: f.origin.trim() || "TLV",
    destination: d.search_destination,
    dates_hint: EXACT_DATES_HINT,
    trip_type: roundTrip ? "round_trip" : "one_way",
    passengers: f.passengers,
    cabin_class: f.cabin_class,
    max_stops: f.direct_only ? 0 : null,
    direct_only: f.direct_only,
    cabin_bag: f.cabin_bag,
    checked_bag: false,
    depart_from: d.depart_date,
    depart_to: d.depart_date,
    nights_min: nights,
    nights_max: nights,
    max_price: null,
    currency: d.currency,
    nearby_airports: false,
  };
}

const MONTH_HINT = (iso: string) => new Date(`${iso.slice(0, 7)}-01T00:00:00`).toLocaleDateString("en", { month: "long", year: "numeric" });

/** "Watch this destination": the departure month Explore found, a trip length
 *  around the chosen one, the same party/stops/bags. The caller adds the price. */
export function destinationWatchRequest(d: ExploreDestination, f: ExploreForm): SearchRequest {
  const roundTrip = f.trip_type === "round_trip";
  const [lo, hi] = DURATION_NIGHTS[f.travel_duration];
  return {
    ...liveSearchRequest(d, f),
    dates_hint: MONTH_HINT(d.depart_date),
    trip_type: roundTrip ? "round_trip" : "one_way",
    depart_from: null,
    depart_to: null,
    nights_min: roundTrip ? lo : null,
    nights_max: roundTrip ? hi : null,
  };
}
