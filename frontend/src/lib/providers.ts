// Fare provenance and fare-API usage, as shown on deal cards and the Search tab.
import { formatPrice } from "./format";
import type { AgentEvent } from "../api";
import type { PriceInsights, StoredDeal, UsageReport } from "../types";

export type SourceTone = "live" | "cached" | "web";

export interface SourceBadgeInfo {
  label: string;
  tip: string;
  tone: SourceTone;
}

const HOUR = 3600_000;
const LIVE_FOR_HOURS = 6; // a Google Flights price younger than this still reads "live"

/** "5h", "2d" — the compact age used inside badges. */
export function compactAge(ms: number): string {
  const hours = Math.max(ms, 0) / HOUR;
  if (hours < 1) return "<1h";
  if (hours < 36) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

/** The source badge for a deal, or null for legacy rows that don't say. */
export function sourceBadge(deal: Pick<StoredDeal, "source" | "seen_at" | "found_at">, now = Date.now()): SourceBadgeInfo | null {
  const seen = deal.seen_at ? new Date(deal.seen_at).getTime() : NaN;
  const age = Number.isNaN(seen) ? null : now - seen;
  switch (deal.source) {
    case "google_flights":
      if (age == null || age < LIVE_FOR_HOURS * HOUR) {
        return { label: "Google Flights · live", tip: "Exact fare from a live Google Flights search for these dates.", tone: "live" };
      }
      return {
        label: `Google Flights · ${compactAge(age)} ago`,
        tip: "Exact fare from a Google Flights search; prices may have moved since.",
        tone: "cached",
      };
    case "google_explore":
      return {
        label: age == null ? "Google Explore · estimate" : `Google Explore · ${compactAge(age)}`,
        tip: "Cheapest price Google Travel Explore showed for these dates — not a confirmed itinerary. Check it on Google Flights.",
        tone: "cached",
      };
    case "aviasales":
      return {
        label: age == null ? "Aviasales · cached" : `Aviasales · cached ${compactAge(age)}`,
        tip: "Price other travellers saw recently on Aviasales (per adult, multiplied for your party, bags not included). Confirm on the booking site.",
        tone: "cached",
      };
    case "web":
      return { label: "Web search", tip: "Found by the AI agent on the live web.", tone: "web" };
    default:
      return null;
  }
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The console's search tally by kind: "2 Google searches · 1 cached-fare lookup · 3 web searches".
 *  "web searches" are the LLM agent's only; fare-API calls carry a `source`. */
export function searchTally(events: Pick<AgentEvent, "kind" | "source">[]): string {
  const n = { google: 0, aviasales: 0, web: 0 };
  for (const e of events) if (e.kind === "search") n[e.source ?? "web"] += 1;
  const parts = [
    n.google && plural(n.google, "Google search", "Google searches"),
    n.aviasales && plural(n.aviasales, "cached-fare lookup"),
    n.web && plural(n.web, "web search", "web searches"),
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "no searches yet";
}

const LEVEL_WORDS: Record<string, string> = { low: "this is low", typical: "this is typical", high: "this is high" };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Nov 12–16", "Nov 28 – Dec 3", "Nov 12" (one way); "" when the dates aren't ISO. */
export function pairLabel(depart: string, ret: string): string {
  const parse = (s: string) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
    return m ? { mon: MONTHS[Number(m[2]) - 1], day: Number(m[3]) } : null;
  };
  const a = parse(depart);
  if (!a) return "";
  const b = parse(ret);
  if (!b) return `${a.mon} ${a.day}`;
  return a.mon === b.mon ? `${a.mon} ${a.day}–${b.day}` : `${a.mon} ${a.day} – ${b.mon} ${b.day}`;
}

/** "Typical for Nov 12–16: $540–$760 · this is low", or null without data.
 *  Google's insights are for ONE date pair, so the line names it. */
export function insightsLine(ins: PriceInsights | null | undefined): string | null {
  if (!ins) return null;
  const level = LEVEL_WORDS[ins.price_level?.toLowerCase()] ?? "";
  const dates = pairLabel(ins.depart_date, ins.return_date);
  if (ins.typical_low != null && ins.typical_high != null) {
    const range = `${formatPrice(ins.typical_low, ins.currency)}–${formatPrice(ins.typical_high, ins.currency)}`;
    return `Typical for ${dates || "this route"}: ${range}${level ? ` · ${level}` : ""}`;
  }
  return level ? `Google Flights${dates ? ` for ${dates}` : ""}: ${level}` : null;
}

export function priceLevelTone(ins: PriceInsights | null | undefined): "good" | "bad" | "neutral" {
  const level = ins?.price_level?.toLowerCase();
  return level === "low" ? "good" : level === "high" ? "bad" : "neutral";
}

export interface UsageMeterInfo {
  text: string;
  used: number;
  budget: number;
  low: boolean; // 10% or less left
}

/** The Watches tab / digest warning when active watches may outspend the monthly budget, else null. */
export function usageWarning(report: UsageReport | null | undefined): string | null {
  return report?.warning ? report.warning : null;
}

/** The live-search meter, or null when live search isn't configured. */
export function usageMeter(report: UsageReport | null | undefined): UsageMeterInfo | null {
  const serp = report?.providers.find((p) => p.provider === "serpapi");
  if (!serp || !serp.configured || serp.budget == null) return null;
  const left = serp.left ?? Math.max(serp.budget - serp.used, 0);
  return {
    text: `Live searches this month: ${serp.used} / ${serp.budget}`,
    used: serp.used,
    budget: serp.budget,
    low: left <= Math.ceil(serp.budget * 0.1),
  };
}
