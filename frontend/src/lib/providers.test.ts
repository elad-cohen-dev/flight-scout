import { describe, expect, it } from "vitest";
import { compactAge, insightsLine, priceLevelTone, sourceBadge, usageMeter } from "./providers";
import type { PriceInsights } from "../types";

const NOW = Date.parse("2026-10-01T12:00:00Z");
const deal = (source: string, seen_at = "") =>
  ({ source, seen_at, found_at: "2026-10-01T12:00:00Z" }) as Parameters<typeof sourceBadge>[0];

describe("source badge", () => {
  it("labels live Google Flights fares and ages old ones", () => {
    expect(sourceBadge(deal("google_flights", "2026-10-01T10:00:00Z"), NOW)).toMatchObject({ label: "Google Flights · live", tone: "live" });
    expect(sourceBadge(deal("google_flights", "2026-09-30T00:00:00Z"), NOW)?.label).toBe("Google Flights · 2d ago");
  });

  it("shows how old a cached Aviasales price is", () => {
    expect(sourceBadge(deal("aviasales", "2026-09-29T12:00:00Z"), NOW)?.label).toBe("Aviasales · cached 2d");
    expect(sourceBadge(deal("aviasales", "2026-10-01T07:00:00Z"), NOW)?.label).toBe("Aviasales · cached 5h");
    expect(sourceBadge(deal("aviasales"), NOW)).toMatchObject({ label: "Aviasales · cached", tone: "cached" });
  });

  it("marks web finds and leaves legacy rows unbadged", () => {
    expect(sourceBadge(deal("web"), NOW)?.tone).toBe("web");
    expect(sourceBadge(deal(""), NOW)).toBeNull();
  });

  it("formats compact ages", () => {
    expect([compactAge(10 * 60_000), compactAge(5 * 3600_000), compactAge(3 * 86400_000)]).toEqual(["<1h", "5h", "3d"]);
  });
});

describe("price insights", () => {
  const ins: PriceInsights = {
    route: "TLV>PRG", currency: "USD", lowest_price: 486, price_level: "low",
    typical_low: 540, typical_high: 760, depart_date: "2026-11-05", return_date: "2026-11-09",
  };

  it("describes the typical range and the level", () => {
    expect(insightsLine(ins)).toBe("Typical for this route: $540–$760 · this is low");
    expect(insightsLine({ ...ins, price_level: "" })).toBe("Typical for this route: $540–$760");
    expect(insightsLine({ ...ins, typical_low: null, typical_high: null, price_level: "high" })).toBe("Google Flights: this is high");
    expect(insightsLine(null)).toBeNull();
    expect([priceLevelTone(ins), priceLevelTone({ ...ins, price_level: "high" }), priceLevelTone(null)]).toEqual(["good", "bad", "neutral"]);
  });
});

describe("usage meter", () => {
  it("shows live searches used against the budget", () => {
    const report = {
      month: "2026-10",
      providers: [
        { provider: "serpapi", configured: true, used: 12, budget: 230, left: 218 },
        { provider: "travelpayouts", configured: true, used: 40, budget: null, left: null },
      ],
    };
    expect(usageMeter(report)).toEqual({ text: "Live searches this month: 12 / 230", used: 12, budget: 230, low: false });
    expect(usageMeter({ ...report, providers: [{ ...report.providers[0], used: 220, left: 10 }] })?.low).toBe(true);
    expect(usageMeter({ ...report, providers: [{ ...report.providers[0], configured: false }] })).toBeNull();
    expect(usageMeter(null)).toBeNull();
  });
});
