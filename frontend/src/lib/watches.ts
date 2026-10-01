import type { AlertSettings, SearchParams, SearchRequest, Watch, WatchCreate, WatchStats, WatchUpdate } from "../types";
import { formatPrice } from "./format";
import type { QueryGroup } from "./history";
import { CABIN_LABELS, exactWindowLabel, formToWatch, requestToForm, totalPassengers } from "./search";
import type { SearchForm } from "./search";

type Criteria = Partial<SearchParams> & { origin: string; destination: string; dates_hint: string; direct_only?: boolean };

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();

export function routeKey(origin: string, destination: string): string {
  return `${norm(origin)}|${norm(destination)}`;
}

/** Everything that makes two searches the same search — not the price cap or currency. */
export function criteriaKey(c: Criteria): string {
  const stops = c.max_stops !== undefined ? c.max_stops : c.direct_only ? 0 : null;
  const p = c.passengers ?? { adults: 1, children: 0, infants: 0 };
  return JSON.stringify([
    routeKey(c.origin, c.destination),
    norm(c.dates_hint),
    c.trip_type ?? "round_trip",
    [p.adults, p.children, p.infants],
    c.cabin_class ?? "economy",
    stops,
    !!c.cabin_bag,
    !!c.checked_bag,
    c.depart_from ?? null,
    c.depart_to ?? null,
    c.nights_min ?? null,
    c.nights_max ?? null,
    !!c.nearby_airports,
  ]);
}

export interface WatchMatch {
  watch: Watch;
  /** true: the watch runs exactly this search; false: it watches the same route with other criteria. */
  exact: boolean;
}

/** The watch behind a History card: the one whose runs these are, else one
 *  created with the same criteria (e.g. from this card, before it has run),
 *  else any watch on the same route. Active watches win ties. */
export function watchForGroup(g: QueryGroup, watches: Watch[]): WatchMatch | null {
  if (g.watchId != null) {
    const own = watches.find((w) => w.id === g.watchId);
    if (own) return { watch: own, exact: true };
  }
  if (!g.request) return null;
  const route = routeKey(g.request.origin, g.request.destination);
  const onRoute = watches
    .filter((w) => routeKey(w.origin, w.destination) === route)
    .sort((a, b) => Number(b.active) - Number(a.active));
  if (onRoute.length === 0) return null;
  const key = criteriaKey(g.request);
  const same = onRoute.find((w) => criteriaKey(w) === key);
  return same ? { watch: same, exact: true } : { watch: onRoute[0], exact: false };
}

/** A default alert threshold ~10% under the latest best price, rounded to a tidy step. */
export function suggestedThreshold(price: number): number {
  const step = price < 100 ? 1 : price < 1000 ? 5 : price < 5000 ? 10 : 50;
  return Math.max(step, Math.round((price * 0.9) / step) * step);
}

/** Watch for the search behind a History card, alerting at `maxPrice`. */
export function watchFromRequest(req: SearchRequest, maxPrice: number, currency: string): WatchCreate {
  return { ...req, max_price: maxPrice, currency };
}

export function watchToForm(w: Watch): SearchForm {
  return requestToForm({ ...w, direct_only: w.max_stops === 0 });
}

/** Editor form -> PATCH body; null when the threshold is missing. The route is fixed. */
export function formToWatchUpdate(form: SearchForm): WatchUpdate | null {
  const watch = formToWatch(form);
  if (!watch) return null;
  const { origin: _origin, destination: _destination, ...patch } = watch;
  return patch;
}

function partyWords(p: SearchParams["passengers"]): string {
  const parts = [`${p.adults} adult${p.adults === 1 ? "" : "s"}`];
  if (p.children) parts.push(`${p.children} child${p.children === 1 ? "" : "ren"}`);
  if (p.infants) parts.push(`${p.infants} infant${p.infants === 1 ? "" : "s"}`);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
}

/** What a watch checks, in plain words, e.g. "Every day, looks for a round trip
 *  from TLV to Greece (a long weekend in November) for 2 adults in economy,
 *  direct flights only, needs a cabin trolley." — a requirement: Google's fares
 *  don't include a checked bag, so the wording mustn't promise the price does. */
export function describeWatch(w: Criteria & { max_price?: number | null }): { search: string; alert: string } {
  const trip = w.trip_type === "one_way" ? "a one-way flight" : "a round trip";
  const window = exactWindowLabel({
    depart_from: w.depart_from ?? null,
    depart_to: w.depart_to ?? null,
    nights_min: w.trip_type === "one_way" ? null : w.nights_min ?? null,
    nights_max: w.trip_type === "one_way" ? null : w.nights_max ?? null,
  });
  const when = window ? `departing ${window}` : `(${w.dates_hint.trim() || "any dates"})`;
  const pax = w.passengers ?? { adults: 1, children: 0, infants: 0 };
  const cabin = CABIN_LABELS[w.cabin_class ?? "economy"].toLowerCase();
  const stops = w.max_stops !== undefined ? w.max_stops : w.direct_only ? 0 : null;
  const bits = [`for ${partyWords(pax)} in ${cabin}`];
  if (stops === 0) bits.push("direct flights only");
  else if (stops != null) bits.push(`at most ${stops} stop${stops === 1 ? "" : "s"}`);
  const bags = [w.cabin_bag && "a cabin trolley", w.checked_bag && "a 20kg checked bag"].filter(Boolean);
  if (bags.length) bits.push(`needs ${bags.join(" and ")}`);
  if (w.nearby_airports) bits.push("also trying nearby airports");
  const search = `Every day, looks for ${trip} from ${w.origin} to ${w.destination} ${when} ${bits.join(", ")}.`;
  const who = totalPassengers(pax) > 1 ? "the total for everyone" : "a fare";
  const alert =
    w.max_price != null && w.max_price > 0
      ? `Alerts you when ${who} is at or under ${formatPrice(w.max_price, w.currency ?? "USD")}.`
      : "Set a max price to get alerts.";
  return { search, alert };
}

// --- smart alerts ---------------------------------------------------------------

/** Mirrors backend AlertSettings defaults (a pre-2.0 server sends no `alerts`). */
export const DEFAULT_ALERTS: AlertSettings = {
  threshold: true,
  new_low: true,
  new_low_days: 30,
  under_typical: true,
  under_typical_pct: 20,
  big_drop: true,
  big_drop_pct: 15,
};

/** Input bounds, matching the backend's validation. */
export const ALERT_LIMITS = { days: [7, 365], pct: [5, 80] } as const;

export function alertSettingsOf(w: Pick<Watch, "alerts">): AlertSettings {
  return { ...DEFAULT_ALERTS, ...(w.alerts ?? {}) };
}

export const RULE_LABELS: Record<string, string> = {
  threshold: "Under target",
  new_low: "New low",
  under_typical: "Under typical",
  big_drop: "Big drop",
};

/** The enabled smart rules as short chips, e.g. ["Lowest in 30 days", "20% under typical"]. */
export function ruleChips(a: AlertSettings): string[] {
  const chips: string[] = [];
  if (a.new_low) chips.push(`Lowest in ${a.new_low_days} days`);
  if (a.under_typical) chips.push(`${a.under_typical_pct}% under typical`);
  if (a.big_drop) chips.push(`${a.big_drop_pct}% drop`);
  return chips;
}

const RULE_PRIORITY = ["new_low", "under_typical", "big_drop", "threshold"];

/** A logged alert's rules, most telling first and folded: "New low +2". */
export function ruleHeadline(rule: string): string {
  const rules = rule.split(",").filter(Boolean);
  if (rules.length === 0) return "";
  const top = [...rules].sort((a, b) => RULE_PRIORITY.indexOf(a) - RULE_PRIORITY.indexOf(b))[0];
  return `${RULE_LABELS[top] ?? top}${rules.length > 1 ? ` +${rules.length - 1}` : ""}`;
}

/** "threshold,new_low" -> "Under target · New low". */
export function ruleWords(rule: string): string {
  return rule
    .split(",")
    .filter(Boolean)
    .map((r) => RULE_LABELS[r] ?? r)
    .join(" · ");
}

export type ScoreTone = "great" | "good" | "fair" | "poor";

/** Deal score 0-100 -> tone + word, e.g. 92 -> great / "Great deal". */
export function scoreTone(score: number): { tone: ScoreTone; label: string } {
  if (score >= 80) return { tone: "great", label: "Great deal" };
  if (score >= 60) return { tone: "good", label: "Good price" };
  if (score >= 40) return { tone: "fair", label: "Typical" };
  return { tone: "poor", label: "Pricey" };
}

/** The smart rules in one plain sentence; "" when they're all off. */
export function describeAlertRules(a: AlertSettings): string {
  const parts: string[] = [];
  if (a.new_low) parts.push(`the lowest price in ${a.new_low_days} days`);
  if (a.under_typical) parts.push(`${a.under_typical_pct}% under the typical price`);
  if (a.big_drop) parts.push(`a ${a.big_drop_pct}% drop since the last check`);
  if (parts.length === 0) return "";
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} or ${parts[parts.length - 1]}` : parts[0];
  return `${a.threshold ? "Also on" : "Alerts on"} ${list}.`;
}

/** Clamp an alert-rule number input to its bounds; NaN/empty -> fallback. */
export function clampRule(value: string | number, [lo, hi]: readonly [number, number], fallback: number): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n) || (typeof value === "string" && value.trim() === "")) return fallback;
  return Math.min(hi, Math.max(lo, Math.round(n)));
}

// --- stats trust basis ------------------------------------------------------------

/** "$285–390" / "950–1,300 CZK", like the alert messages. */
export function priceRange(low: number, high: number, currency: string): string {
  const lo = formatPrice(low, currency);
  const hi = formatPrice(high, currency);
  return lo.endsWith(currency) ? `${lo.slice(0, -currency.length - 1)}–${hi}` : `${lo}–${hi.replace(/^[^\d]+/, "")}`;
}

const checks = (n: number, what: string) => `${n} ${what} check${n === 1 ? "" : "s"}`;

/** What a watch card's numbers rest on, e.g. "Based on 12 verified checks";
 *  "" for legacy-only stats (see legacyNote) and pre-2.1 servers. */
export function basisNote(s: WatchStats): string {
  const n = s.basis_runs ?? 0;
  switch (s.basis) {
    case "verified":
      return `Based on ${checks(n, "verified")}`;
    case "google":
      return n ? `Based on Google's price history + ${checks(n, "verified")}` : "Based on Google's price history";
    case "explore":
      return `Based on ${checks(n, "Google Explore")} — dates' prices, not confirmed fares yet`;
    case "cached":
      return `Based on ${checks(n, "cached-fare")} — not confirmed live yet`;
    default:
      return "";
  }
}

/** True when the card's numbers come from unverified estimates only (hidden behind legacyNote). */
export function legacyOnly(s: WatchStats | null | undefined): boolean {
  return s?.basis === "legacy";
}

/** The muted note for unverified (pre-2.0 / web-search) checks: a summary line
 *  and, when there are numbers to show, the numbers. null when there are none. */
export function legacyNote(s: WatchStats, currency: string): { summary: string; detail: string } | null {
  const old =
    s.basis === "legacy"
      ? { runs: s.runs, low_30d: s.low_30d, typical: s.typical }
      : s.legacy ?? (s.legacy_runs ? { runs: s.legacy_runs, low_30d: null, typical: null } : null);
  if (!old || !old.runs) return null;
  const est = `${old.runs} older estimated check${old.runs === 1 ? "" : "s"}`;
  const nums = [
    old.low_30d != null && `30-day low ${formatPrice(old.low_30d, currency)}`,
    old.typical != null && `typical ${formatPrice(old.typical, currency)}`,
  ].filter(Boolean);
  const why = "web-search estimates, often without bags";
  const detail = nums.length ? `${nums.join(" · ")} — ${why}${s.basis === "legacy" ? "." : "; not used for the score or alerts."}` : "";
  if (s.basis === "legacy") return { summary: `Only ${est} so far — not verified`, detail };
  return { summary: s.legacy ? `${est}, not counted` : `${est} not counted`, detail };
}
