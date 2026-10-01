// Mirrors backend/app/models.py — keep in sync.
export type TripType = "round_trip" | "one_way";
export type CabinClass = "economy" | "premium_economy" | "business" | "first";
export type PriceConfidence = "verified" | "estimated" | "from_price";
/** Where a fare came from: live Google Flights (SerpApi), the Aviasales cache, or the LLM web agent. "" = legacy row. */
export type FareSource = "" | "google_flights" | "aviasales" | "web";

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
  // Data-quality issues from backend normalize.py, e.g. "currency_inferred", "price_too_low",
  // "destination_mismatch" (full list: normalize.FLAGS). Empty = clean or not yet checked.
  quality_flags?: string[];
  source?: FareSource; // set by the backend engine/providers
  seen_at?: string; // ISO time the source saw this price ("" = unknown)
  details_cost?: number; // most live searches "Details & book direct" may use (0/absent = not available)
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
  // interrupted: the server restarted mid-run; nothing re-runs by itself
  status: "running" | "done" | "error" | "cancelled" | "interrupted";
  query: string;
  deals: StoredDeal[] | null;
  summary: Record<string, unknown> | null;
  meta?: SearchMeta | null; // kind=search: sources, Google price insights, notices
  error: string | null;
  request?: Record<string, unknown> | null; // what started it (search body / {watch_id}) — for "Run again"
  created_at?: string | null;
  updated_at?: string | null;
}

/** Google Flights' price level for the searched route (SerpApi price_insights), party total in `currency`. */
export interface PriceInsights {
  route: string; // "TLV>PRG"
  currency: string;
  lowest_price: number | null;
  price_level: string; // "low" | "typical" | "high"
  typical_low: number | null;
  typical_high: number | null;
  depart_date: string;
  return_date: string;
}

export interface SearchMeta {
  sources: string[]; // google_flights, aviasales, web — in that order
  price_insights: PriceInsights | null;
  live_searches: number; // paid SerpApi searches this run
  messages: string[]; // e.g. "This month's live Google Flights budget … is used up"
  decision: string; // watch runs: the pre-screen decision
}

export interface ProviderUsage {
  provider: string; // "serpapi" | "travelpayouts"
  configured: boolean;
  used: number; // calls this calendar month (UTC), cache hits excluded
  budget: number | null; // null = uncapped
  left: number | null;
}

export interface UsageReport {
  month: string; // YYYY-MM
  providers: ProviderUsage[];
}

/** Per-watch alert rules (backend AlertSettings). */
export interface AlertSettings {
  threshold: boolean; // best price at or under max_price
  new_low: boolean; // lowest in new_low_days (needs 5 earlier checks)
  new_low_days: number;
  under_typical: boolean; // under_typical_pct under the 30-day median
  under_typical_pct: number;
  big_drop: boolean; // big_drop_pct cheaper than the previous check
  big_drop_pct: number;
}

/** Price stats over a watch's runs, in the watch currency (computed by the API). */
export interface WatchStats {
  currency: string;
  runs: number;
  last_best: number | null;
  last_run_at: string | null;
  low_30d: number | null;
  typical: number | null; // median of the last 30 days' run-bests
  all_time_low: number | null;
  score: number | null; // 0-100 deal score of the last best price
  // Which trust tier the numbers rest on (absent from pre-2.1 servers):
  // verified = own Google Flights runs; google = Google's price history (+ own
  // verified runs); cached = Aviasales cache runs; legacy = unverified estimates only.
  basis?: "verified" | "google" | "cached" | "legacy";
  basis_runs?: number; // runs of that tier (verified ones for basis=google)
  history_points?: number; // Google price-history points of the last 30 days used
  typical_low?: number | null; // Google's typical range (basis=google)
  typical_high?: number | null;
  legacy_runs?: number; // unverified runs left out of the numbers
  legacy?: { runs: number; low_30d: number | null; typical: number | null; all_time_low: number | null } | null;
}

export interface AlertRecord {
  watch_id: number;
  fingerprint: string;
  price: number;
  currency: string;
  rule: string; // comma-separated: threshold,new_low,under_typical,big_drop
  score: number | null;
  sent_at: string;
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
  alerts?: AlertSettings;
  stats?: WatchStats | null; // null before the first comparable run
  last_alert?: AlertRecord | null;
}

export interface WatchCreate extends Partial<SearchParams> {
  origin: string;
  destination: string;
  dates_hint: string;
  max_price: number;
  direct_only?: boolean;
  cabin_bag: boolean;
  checked_bag: boolean;
  alerts?: Partial<AlertSettings>;
}

/** PATCH /api/watches/{id} body: any subset. The route can't be edited;
 *  `active: false` pauses the watch, `true` resumes it. */
export interface WatchUpdate extends Partial<Omit<SearchParams, "max_price">> {
  dates_hint?: string;
  max_price?: number;
  direct_only?: boolean;
  active?: boolean;
  alerts?: Partial<AlertSettings>;
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

// --- deal details on demand (GET/POST /api/deals/{id}/details, backend app/details.py) ---

export interface DetailsLeg {
  origin: string;
  destination: string;
  depart_date: string;
  depart_time: string;
  arrive_date: string;
  arrive_time: string;
  airline: string;
  flight_numbers: string;
  duration: string;
  stops: number;
  layovers: string[];
}

export interface BagFee {
  text: string; // Google's wording; "" = not stated
  fee: number | null; // exact per traveller (0 = free); null = unknown or a range
}

export interface SellerLink {
  part: "together" | "departing" | "returning";
  label: string;
  url: string; // signed /api/deals/{id}/book/{option} page; "" = no booking link
}

export interface Seller {
  index: number;
  book_with: string;
  airline: boolean; // sold by the airline itself
  price: number | null; // party total
  option_title: string;
  extensions: string[];
  carry_on: BagFee;
  checked: BagFee;
  bag_notes: string[];
  price_incl_bags: number | null;
  bags_verified: boolean;
  separate_tickets: boolean;
  phone: string;
  links: SellerLink[];
}

export interface DealDetails {
  deal_id: number;
  status: "ready" | "needs_fetch" | "expired" | "unavailable";
  message: string;
  cost: number; // live searches a fetch would use now
  left: number; // live searches left this month
  budget: number;
  spent: number;
  fetched_at: string;
  currency: string;
  outbound: DetailsLeg | null;
  return_leg: DetailsLeg | null;
  sellers: Seller[]; // cheapest first, airlines win ties
  bags_requested: ("carry_on" | "checked")[];
  verified_incl_bags: boolean;
  price_incl_bags: number | null;
}
