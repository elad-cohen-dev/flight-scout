import type { StoredDeal } from "../types";
import { criteriaChips, exactWindowLabel, parseSearchQuery, rerunRequest } from "./search";

/** One search run: all deals stored with the same label + timestamp. */
export interface Run {
  key: string;
  foundAt: string;
  deals: StoredDeal[]; // cheapest first
  minPrice: number;
  currency: string;
}

/** Every run of the same search (same criteria), collapsed into one entry. */
export interface QueryGroup {
  key: string;
  route: string; // "TLV → Prague"
  datesHint: string;
  chips: string[];
  runs: Run[]; // newest first
  latest: Run;
  previous: Run | null;
  bestEver: number;
  watchId: number | null;
  hasDirect: boolean;
  withBags: boolean;
  haystack: string; // lowercased text the filter box matches against
}

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();

function fingerprint(d: StoredDeal): string {
  return [d.airline, d.origin_airport || d.origin, d.destination_airport || d.destination, d.depart_date, d.return_date, Math.round(d.price)]
    .map((v) => String(v).toLowerCase())
    .join("|");
}

function groupKey(deal: StoredDeal): string {
  const label = norm(deal.search_query.replace(/\s*\[watch #\d+\]/, ""));
  const sp = deal.search_params;
  if (!sp) return label;
  // Criteria the label may not spell out still make it a different search.
  const { passengers, cabin_class, trip_type, depart_from, depart_to, nights_min, nights_max, nearby_airports, max_stops } = sp;
  return `${label}|${JSON.stringify([passengers, cabin_class, trip_type, depart_from, depart_to, nights_min, nights_max, nearby_airports, max_stops])}`;
}

export function groupHistory(deals: StoredDeal[]): QueryGroup[] {
  const runs = new Map<string, Run & { gkey: string }>();
  for (const deal of deals) {
    const key = `${deal.search_query}|${deal.found_at}`;
    let run = runs.get(key);
    if (!run) {
      run = { key, gkey: groupKey(deal), foundAt: deal.found_at, deals: [], minPrice: Infinity, currency: deal.currency };
      runs.set(key, run);
    }
    run.deals.push(deal);
    if (deal.price < run.minPrice) {
      run.minPrice = deal.price;
      run.currency = deal.currency;
    }
  }

  const byQuery = new Map<string, Run[]>();
  for (const run of runs.values()) {
    run.deals.sort((a, b) => a.price - b.price);
    const list = byQuery.get(run.gkey) ?? [];
    list.push(run);
    byQuery.set(run.gkey, list);
  }

  const groups: QueryGroup[] = [];
  for (const [key, list] of byQuery) {
    list.sort((a, b) => b.foundAt.localeCompare(a.foundAt));
    // With dedupe off, a fare re-found by several runs appears once per run;
    // count those sightings so the latest run can say "seen 4×".
    const seen = new Map<string, { n: number; first: string }>();
    for (const run of list) {
      for (const d of run.deals) {
        const fp = fingerprint(d);
        const s = seen.get(fp);
        if (s) {
          s.n += 1;
          if (run.foundAt < s.first) s.first = run.foundAt;
        } else seen.set(fp, { n: 1, first: run.foundAt });
      }
    }
    for (const run of list) {
      run.deals = run.deals.map((d) => {
        const s = seen.get(fingerprint(d))!;
        return s.n > Math.max(1, d.times_seen) ? { ...d, times_seen: s.n, first_found_at: s.first } : d;
      });
    }

    const latest = list[0];
    const sample = latest.deals[0];
    const parsed = parseSearchQuery(sample.search_query);
    const req = rerunRequest(sample);
    const all = list.flatMap((r) => r.deals);
    const watchRun = list.map((r) => parseSearchQuery(r.deals[0].search_query)?.watchId).find((id) => id != null);
    groups.push({
      key,
      route: `${parsed?.origin ?? sample.origin} → ${parsed?.destination ?? sample.destination}`,
      datesHint: (sample.search_params && exactWindowLabel(sample.search_params)) || parsed?.dates_hint || "",
      chips: req ? criteriaChips(req) : [],
      runs: list,
      latest,
      previous: list[1] ?? null,
      bestEver: Math.min(...list.map((r) => r.minPrice)),
      watchId: watchRun ?? null,
      hasDirect: latest.deals.some((d) => d.stops === 0),
      withBags: !!req && (req.cabin_bag || req.checked_bag),
      haystack: norm(
        [sample.search_query, ...all.flatMap((d) => [d.airline, d.destination, d.destination_airport, d.origin_airport])].join(" ")
      ),
    });
  }
  return groups;
}

export type Section = { title: string; groups: QueryGroup[] };

const DAY = 86_400_000;

export function recencyTitle(iso: string, now = new Date()): string {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = new Date(iso).getTime();
  if (t >= startOfToday) return "Today";
  if (t >= startOfToday - DAY) return "Yesterday";
  if (t >= startOfToday - 6 * DAY) return "This week";
  if (t >= startOfToday - 29 * DAY) return "This month";
  return "Older";
}

/** Sections in display order; groups keep the order they arrive in. */
export function sectionize(groups: QueryGroup[], by: "recent" | "route"): Section[] {
  const sections = new Map<string, QueryGroup[]>();
  for (const g of groups) {
    const title = by === "recent" ? recencyTitle(g.latest.foundAt) : g.route;
    const list = sections.get(title) ?? [];
    list.push(g);
    sections.set(title, list);
  }
  const result = [...sections].map(([title, gs]) => ({ title, groups: gs }));
  if (by === "route") result.sort((a, b) => a.title.localeCompare(b.title));
  else {
    const order = ["Today", "Yesterday", "This week", "This month", "Older"];
    result.sort((a, b) => order.indexOf(a.title) - order.indexOf(b.title));
  }
  return result;
}
