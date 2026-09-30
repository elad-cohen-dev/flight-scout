// Mirrors backend/app/models.py — keep in sync.
export type TripType = "round_trip" | "one_way";
export type CabinClass = "economy" | "premium_economy" | "business" | "first";
export type PriceConfidence = "verified" | "estimated" | "from_price";

export interface Passengers {
  adults: number;
  children: number;
  infants: number;
}

/** Structured search criteria (backend SearchParams). All optional on requests. */
export interface SearchParams {
  trip_type: TripType;
  passengers: Passengers;
  cabin_class: CabinClass;
  max_stops: number | null; // null = any, 0 = direct only
  cabin_bag: boolean;
  checked_bag: boolean;
  depart_from: string | null; // YYYY-MM-DD
  depart_to: string | null;
  nights_min: number | null;
  nights_max: number | null;
  max_price: number | null;
  currency: string;
  nearby_airports: boolean;
}

export interface BookingLink {
  label: string;
  url: string;
  kind: "airline" | "google_flights" | "skyscanner" | "kayak" | "momondo" | "other";
  prefilled: string[]; // subset of route, dates, passengers, cabin, bags, stops
}

export interface StoredDeal {
  id: number;
  search_query: string;
  found_at: string;
  times_seen: number;
  first_found_at: string | null;
  origin: string;
  destination: string;
  airline: string;
  price: number;
  currency: string;
  depart_date: string;
  return_date: string;
  duration: string;
  stops: number;
  booking_url: string;
  source_url: string;
  notes: string;
  origin_airport: string;
  destination_airport: string;
  depart_time: string;
  arrive_time: string;
  return_depart_time: string;
  flight_numbers: string;
  airline_code: string;
  price_per_person: number | null;
  price_confidence: PriceConfidence;
  booking_links: BookingLink[];
  search_params: SearchParams | null; // null for legacy rows
  compare_price: number | null; // price in compare_currency, for ranking across source currencies
  compare_currency: string; // the run's preferred currency (USD for legacy rows)
  route_key: string; // canonical route id, e.g. "TLV>PRG" (same for "Prague" / "PRG" / "Prague [PRG]")
}

export interface SearchRequest extends Partial<SearchParams> {
  origin: string;
  destination: string;
  dates_hint: string;
  direct_only?: boolean; // legacy alias for max_stops = 0
  cabin_bag: boolean;
  checked_bag: boolean;
}

export interface Job {
  job_id: string;
  kind: "search" | "watch_run";
  status: "running" | "done" | "error" | "cancelled";
  query: string;
  deals: StoredDeal[] | null;
  summary: Record<string, unknown> | null;
  error: string | null;
}

export interface Watch extends SearchParams {
  id: number;
  origin: string;
  destination: string;
  dates_hint: string;
  max_price: number;
  currency: string;
  direct_only: boolean;
  active: boolean;
  created_at: string;
  last_run_at: string | null;
  last_best_price: number | null;
}

export interface WatchCreate extends Partial<SearchParams> {
  origin: string;
  destination: string;
  dates_hint: string;
  max_price: number;
  direct_only?: boolean;
  cabin_bag: boolean;
  checked_bag: boolean;
}

/** PATCH /api/watches/{id} body: any subset. The route can't be edited;
 *  `active: false` pauses the watch, `true` resumes it. */
export interface WatchUpdate extends Partial<Omit<SearchParams, "max_price">> {
  dates_hint?: string;
  max_price?: number;
  direct_only?: boolean;
  active?: boolean;
}

export interface HistoryPoint {
  found_at: string;
  price: number;
  deals: number;
}

export interface RouteHistory {
  route: string;
  route_key: string;
  currency: string;
  points: HistoryPoint[];
}

/** Deals grouped by search run (same query + same found_at timestamp). */
export interface SearchGroup {
  key: string;
  query: string;
  foundAt: string;
  deals: StoredDeal[];
  minPrice: number;
}
