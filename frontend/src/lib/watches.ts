import type { SearchParams, SearchRequest, Watch, WatchCreate, WatchUpdate } from "../types";
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
 *  direct flights only, with a cabin trolley included in the price." */
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
  if (bags.length) bits.push(`with ${bags.join(" and ")} included in the price`);
  if (w.nearby_airports) bits.push("also trying nearby airports");
  const search = `Every day, looks for ${trip} from ${w.origin} to ${w.destination} ${when} ${bits.join(", ")}.`;
  const who = totalPassengers(pax) > 1 ? "the total for everyone" : "a fare";
  const alert =
    w.max_price != null && w.max_price > 0
      ? `Alerts you when ${who} is at or under ${formatPrice(w.max_price, w.currency ?? "USD")}.`
      : "Set a max price to get alerts.";
  return { search, alert };
}
