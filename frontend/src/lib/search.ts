import type { CabinClass, Passengers, SearchParams, SearchRequest, StoredDeal, TripType, WatchCreate } from "../types";

export const DEFAULT_DATES_HINT = "anytime in the next 3 months";

export const CABIN_LABELS: Record<CabinClass, string> = {
  economy: "Economy",
  premium_economy: "Premium economy",
  business: "Business",
  first: "First",
};

export const PASSENGER_LIMITS = { total: 9, adults: [1, 9], children: [0, 8], infants: [0, 4] } as const;

/** UI-only form state: number inputs stay strings so they can be empty. */
export interface SearchForm {
  origin: string;
  destination: string;
  trip_type: TripType;
  passengers: Passengers;
  cabin_class: CabinClass;
  max_stops: number | null;
  cabin_bag: boolean;
  checked_bag: boolean; // kept = checked_bags > 0, for older code paths
  checked_bags: number; // whole party, 0..2 per seated traveller
  date_mode: "flexible" | "exact";
  dates_hint: string;
  depart_from: string;
  depart_to: string;
  nights_min: string;
  nights_max: string;
  max_price: string;
  currency: string;
  nearby_airports: boolean;
}

export const DEFAULT_FORM: SearchForm = {
  origin: "TLV",
  destination: "",
  trip_type: "round_trip",
  passengers: { adults: 1, children: 0, infants: 0 },
  cabin_class: "economy",
  max_stops: null,
  cabin_bag: false,
  checked_bag: false,
  checked_bags: 0,
  date_mode: "flexible",
  dates_hint: DEFAULT_DATES_HINT,
  depart_from: "",
  depart_to: "",
  nights_min: "",
  nights_max: "",
  max_price: "",
  currency: "USD",
  nearby_airports: false,
};

const FORM_KEY = "fs_last_search";

export function loadForm(): SearchForm {
  try {
    const raw = localStorage.getItem(FORM_KEY);
    if (!raw) return DEFAULT_FORM;
    const saved = JSON.parse(raw) as Partial<SearchForm>;
    const form = { ...DEFAULT_FORM, ...saved, passengers: { ...DEFAULT_FORM.passengers, ...saved.passengers } };
    // A pre-3.0 form had only the checked-bag checkbox: one per seated traveller.
    const checked_bags = saved.checked_bags ?? checkedBagCount(form);
    return { ...form, checked_bags, checked_bag: checked_bags > 0 };
  } catch {
    return DEFAULT_FORM;
  }
}

export function saveForm(form: SearchForm): void {
  try {
    localStorage.setItem(FORM_KEY, JSON.stringify(form));
  } catch {
    // storage unavailable (private mode / quota) — remembering is best-effort
  }
}

export function totalPassengers(p: Passengers): number {
  return p.adults + p.children + p.infants;
}

/** Checked bags a party can ask for: up to two per seated traveller. */
export function maxCheckedBags(p: Passengers): number {
  return Math.max(1, p.adults + p.children) * 2;
}

/** The checked-bag count a request means: its count, else one per seated
 *  traveller when only the legacy flag is set (the backend's rule). */
export function checkedBagCount(c: { checked_bags?: number | null; checked_bag?: boolean; passengers?: Passengers | null }): number {
  if (c.checked_bags) return c.checked_bags;
  if (!c.checked_bag) return 0;
  const p = c.passengers ?? { adults: 1, children: 0, infants: 0 };
  return Math.max(1, p.adults + p.children);
}

export function passengersLabel(p: Passengers): string {
  const n = totalPassengers(p);
  return n === 1 ? "1 traveller" : `${n} travellers`;
}

const numOrNull = (s: string): number | null => (s.trim() === "" || Number.isNaN(Number(s)) ? null : Number(s));

// With an exact window the structured fields carry the dates (and the backend
// labels them), so the free-text hint just says so.
export const EXACT_DATES_HINT = "specific dates";

/** "Nov 5 – Nov 12 · 4–6 nights" for a structured date window, or null. */
export function exactWindowLabel(p: Pick<SearchParams, "depart_from" | "depart_to" | "nights_min" | "nights_max">): string | null {
  if (!p.depart_from) return null;
  const fmt = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  let text = p.depart_to && p.depart_to !== p.depart_from ? `${fmt(p.depart_from)} – ${fmt(p.depart_to)}` : fmt(p.depart_from);
  const lo = p.nights_min;
  const hi = p.nights_max;
  if (lo != null || hi != null) text += ` · ${lo != null && hi != null && lo !== hi ? `${lo}–${hi}` : lo ?? hi} nights`;
  return text;
}

export function formToRequest(f: SearchForm): SearchRequest {
  const exact = f.date_mode === "exact" && !!f.depart_from;
  const roundTrip = f.trip_type === "round_trip";
  const checked = Math.min(Math.max(0, Math.round(f.checked_bags ?? 0)), maxCheckedBags(f.passengers));
  return {
    origin: f.origin.trim(),
    destination: f.destination.trim(),
    dates_hint: exact ? EXACT_DATES_HINT : f.dates_hint.trim() || DEFAULT_DATES_HINT,
    trip_type: f.trip_type,
    passengers: f.passengers,
    cabin_class: f.cabin_class,
    max_stops: f.max_stops,
    direct_only: f.max_stops === 0, // legacy alias, for older backends
    cabin_bag: f.cabin_bag,
    checked_bag: checked > 0, // legacy flag, for older backends
    checked_bags: checked,
    depart_from: exact ? f.depart_from : null,
    depart_to: exact ? f.depart_to || f.depart_from : null,
    nights_min: exact && roundTrip ? numOrNull(f.nights_min) : null,
    nights_max: exact && roundTrip ? numOrNull(f.nights_max) : null,
    max_price: numOrNull(f.max_price),
    currency: f.currency,
    nearby_airports: f.nearby_airports,
  };
}

export function formToWatch(f: SearchForm): WatchCreate | null {
  const req = formToRequest(f);
  if (req.max_price == null || req.max_price <= 0) return null;
  return { ...req, max_price: req.max_price };
}

/** Reverse of formToRequest, used to reflect a re-run in the form. */
export function requestToForm(req: SearchRequest, base: SearchForm = DEFAULT_FORM): SearchForm {
  const exact = !!req.depart_from;
  return {
    ...base,
    origin: req.origin,
    destination: req.destination,
    trip_type: req.trip_type ?? base.trip_type,
    passengers: req.passengers ?? base.passengers,
    cabin_class: req.cabin_class ?? base.cabin_class,
    max_stops: req.max_stops !== undefined ? req.max_stops : req.direct_only ? 0 : null,
    cabin_bag: req.cabin_bag,
    checked_bag: checkedBagCount(req) > 0,
    checked_bags: checkedBagCount(req),
    date_mode: exact ? "exact" : "flexible",
    dates_hint: exact ? base.dates_hint : req.dates_hint,
    depart_from: req.depart_from ?? "",
    depart_to: req.depart_to ?? "",
    nights_min: req.nights_min != null ? String(req.nights_min) : "",
    nights_max: req.nights_max != null ? String(req.nights_max) : "",
    max_price: req.max_price != null ? String(req.max_price) : "",
    currency: req.currency ?? base.currency,
    nearby_airports: req.nearby_airports ?? false,
  };
}

// Flags the backend's models.search_label() appends inside the parens, in order.
const LABEL_FLAGS = ["direct only", "cabin bag", "checked 20kg bag"];
// "3 checked bags": only when the count isn't one per seated traveller.
const CHECKED_BAGS_FLAG = /^(\d+) checked bags?$/;
// Criteria flags (backend models._criteria_flags) sit between the hint and the legacy flags.
const CRITERIA_FLAGS = [
  /^depart (\d{4}-\d{2}-\d{2} to|from|by) \d{4}-\d{2}-\d{2}$/,
  /^(\d+(-\d+|\+)?|up to \d+) nights$/,
  /^one-way$/,
  /^\d+ adults?( \+ \d+ child(ren)?)?( \+ \d+ infants?)?$/,
  /^(premium economy|business|first)$/,
  /^max \d stops?$/,
  CHECKED_BAGS_FLAG,
  /^nearby airports$/,
  /^up to \d+ [A-Z]{3}$/,
];

export interface ParsedLabel {
  origin: string;
  destination: string;
  dates_hint: string;
  direct_only: boolean;
  cabin_bag: boolean;
  checked_bag: boolean;
  checked_bags: number | null; // the label's explicit count; null = one per seated traveller when checked_bag
  watchId: number | null;
}

/** Recover search criteria from a stored label, e.g.
 *  "TLV -> Prague (October, direct only, cabin bag, checked 20kg bag) [watch #5]".
 *  Must stay in sync with the backend's models.search_label(). */
export function parseSearchQuery(query: string): ParsedLabel | null {
  const m = query.match(/^(.+?)\s*->\s*(.+?)\s*\((.+)\)\s*(?:\[watch #(\d+)\])?\s*$/);
  if (!m) return null;
  const parts = m[3].split(/,\s*/);
  const flags = new Set<string>();
  // Flags follow the free-text hint in a fixed order (criteria flags, then the
  // legacy ones), so peel them off the tail; what's left is the hint.
  while (parts.length > 1 && LABEL_FLAGS.includes(parts[parts.length - 1].trim())) {
    flags.add(parts.pop()!.trim());
  }
  let checkedBags: number | null = null;
  while (parts.length > 1 && CRITERIA_FLAGS.some((re) => re.test(parts[parts.length - 1].trim()))) {
    const m = CHECKED_BAGS_FLAG.exec(parts.pop()!.trim());
    if (m) checkedBags = Number(m[1]);
  }
  return {
    origin: m[1].trim(),
    destination: m[2].trim(),
    dates_hint: parts.join(", ").trim(),
    direct_only: flags.has("direct only"),
    cabin_bag: flags.has("cabin bag"),
    checked_bag: flags.has("checked 20kg bag") || !!checkedBags,
    checked_bags: checkedBags,
    watchId: m[4] ? Number(m[4]) : null,
  };
}

/** Criteria to re-run the search that found `deal`: its stored params when present,
 *  else whatever the legacy label encodes. */
export function rerunRequest(deal: StoredDeal): SearchRequest | null {
  const parsed = parseSearchQuery(deal.search_query);
  const origin = parsed?.origin ?? deal.origin;
  const destination = parsed?.destination ?? deal.destination;
  if (!origin || !destination) return null;
  const dates_hint = parsed?.dates_hint || DEFAULT_DATES_HINT;
  const sp = deal.search_params;
  if (sp) {
    return { ...sp, origin, destination, dates_hint, direct_only: sp.max_stops === 0 };
  }
  return {
    origin,
    destination,
    dates_hint,
    direct_only: parsed?.direct_only ?? false,
    max_stops: parsed?.direct_only ? 0 : null,
    cabin_bag: parsed?.cabin_bag ?? false,
    checked_bag: parsed?.checked_bag ?? false,
    ...(parsed?.checked_bags ? { checked_bags: parsed.checked_bags } : {}),
  };
}

/** "Check this date live": the search that found `deal`, narrowed to its exact
 *  date pair (an ISO pair is the exact-dates path: one Google Flights search). */
export function checkLiveRequest(deal: StoredDeal): SearchRequest | null {
  const req = rerunRequest(deal);
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  if (!req || !iso.test(deal.depart_date)) return null;
  const roundTrip = (req.trip_type ?? "round_trip") === "round_trip";
  if (roundTrip && !iso.test(deal.return_date)) return null;
  return {
    ...req,
    dates_hint: roundTrip ? `${deal.depart_date} to ${deal.return_date}` : deal.depart_date,
    depart_from: null,
    depart_to: null,
    nights_min: null,
    nights_max: null,
  };
}

/** Effective criteria behind a deal, merging stored params with the legacy label. */
export function dealCriteria(deal: StoredDeal): Pick<SearchParams, "cabin_bag" | "checked_bag" | "max_stops"> & {
  checked_bags: number;
  passengers: Passengers | null;
  cabin_class: CabinClass | null;
} {
  const sp = deal.search_params;
  if (sp)
    return {
      cabin_bag: sp.cabin_bag,
      checked_bag: sp.checked_bag,
      checked_bags: checkedBagCount(sp),
      max_stops: sp.max_stops,
      passengers: sp.passengers,
      cabin_class: sp.cabin_class,
    };
  const p = parseSearchQuery(deal.search_query);
  return {
    cabin_bag: p?.cabin_bag ?? false,
    checked_bag: p?.checked_bag ?? false,
    checked_bags: checkedBagCount({ checked_bags: p?.checked_bags, checked_bag: p?.checked_bag }),
    max_stops: p?.direct_only ? 0 : null,
    passengers: null,
    cabin_class: null,
  };
}

/** Short chips describing a request's non-default criteria. */
export function criteriaChips(req: Partial<SearchParams> & { direct_only?: boolean }): string[] {
  const chips: string[] = [];
  if (req.trip_type === "one_way") chips.push("One way");
  if (req.passengers && totalPassengers(req.passengers) > 1) chips.push(passengersLabel(req.passengers));
  if (req.cabin_class && req.cabin_class !== "economy") chips.push(CABIN_LABELS[req.cabin_class]);
  const stops = req.max_stops !== undefined ? req.max_stops : req.direct_only ? 0 : null;
  if (stops === 0) chips.push("Direct only");
  else if (stops != null) chips.push(`≤${stops} stop${stops > 1 ? "s" : ""}`);
  if (req.cabin_bag) chips.push("Cabin trolley");
  const checked = checkedBagCount(req);
  if (checked) chips.push(`${checked} checked bag${checked === 1 ? "" : "s"}`);
  if (req.nearby_airports) chips.push("Nearby airports");
  return chips;
}
