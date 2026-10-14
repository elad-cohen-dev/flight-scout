import { describe, expect, it } from "vitest";
import type { StoredDeal, Watch } from "../types";
import {
  bagsSummary,
  bookedFromDeal,
  cheapestId,
  dealTotal,
  freshness,
  searchRef,
  sortTrips,
  tripDates,
  tripFromDeal,
  tripFromWatch,
  tripIdFromHash,
  tripPriceLine,
  tripRoute,
} from "./trips";
import type { BagsInfo, Trip } from "./trips";

const bags = (fare: number, min: number, max: number, basis = "table", currency = "USD"): BagsInfo => ({
  basis,
  fare,
  total: (min + max) / 2,
  total_min: min,
  total_max: max,
  currency,
});

const deal = (over: Partial<StoredDeal> & { bags?: BagsInfo | null; compare_total?: number | null } = {}) =>
  ({
    id: 1,
    search_query: "TLV -> Rome [FCO] (December, 2 adults + 2 children)",
    found_at: "2026-10-14T08:00:00Z",
    seen_at: "",
    origin: "TLV",
    destination: "Rome",
    airline: "Wizz Air",
    flight_numbers: "W6 1234",
    price: 800,
    currency: "USD",
    depart_date: "2026-12-20",
    return_date: "2026-12-27",
    compare_price: 800,
    compare_currency: "USD",
    search_params: { passengers: { adults: 2, children: 2, infants: 0 } },
    ...over,
  }) as unknown as StoredDeal & { bags?: BagsInfo | null; compare_total?: number | null };

describe("dealTotal", () => {
  it("prefers the price with bags (compare_total), then compare_price, then the raw price with bags", () => {
    expect(dealTotal(deal({ compare_total: 950, compare_currency: "ILS" }))).toEqual({ price: 950, currency: "ILS" });
    expect(dealTotal(deal({ compare_total: null }))).toEqual({ price: 800, currency: "USD" });
    expect(dealTotal(deal({ compare_total: null, compare_price: null, bags: bags(800, 900, 1000) }))).toEqual({ price: 950, currency: "USD" });
  });
});

describe("bagsSummary", () => {
  it("shows what bags add, as a range when the fee isn't exact", () => {
    expect(bagsSummary(deal({ bags: bags(800, 900, 1000) }))).toEqual({ text: "+$100–200", tip: "airline fee table" });
    expect(bagsSummary(deal({ bags: bags(800, 960, 960, "seller") }))!.text).toBe("+$160");
  });
  it("says included when the fare has them, and nothing when no bags were asked", () => {
    expect(bagsSummary(deal({ bags: bags(800, 800, 800, "google") }))!.text).toBe("Included");
    expect(bagsSummary(deal({ bags: bags(800, 800, 800, "as_quoted") }))!.text).toBe("Included");
    expect(bagsSummary(deal({ bags: { ...bags(800, 800, 800), basis: "none" } }))).toBeNull();
    expect(bagsSummary(deal({ bags: null }))).toBeNull();
  });
});

describe("freshness", () => {
  const now = Date.parse("2026-10-14T12:00:00Z");
  it("ages prices by the source's seen_at, else when stored", () => {
    expect(freshness(deal(), now)).toBe("fresh");
    expect(freshness(deal({ found_at: "2026-10-12T12:00:00Z" }), now)).toBe("aging");
    expect(freshness(deal({ found_at: "2026-10-01T12:00:00Z" }), now)).toBe("stale");
    expect(freshness(deal({ found_at: "2026-10-14T11:00:00Z", seen_at: "2026-10-01T00:00:00Z" }), now)).toBe("stale");
  });
});

describe("cheapestId", () => {
  it("picks the cheapest with bags among the most common currency", () => {
    const list = [
      deal({ id: 1, compare_total: 900 }),
      deal({ id: 2, compare_total: 850 }),
      deal({ id: 3, compare_total: 100, compare_currency: "EUR" }),
    ];
    expect(cheapestId(list)).toBe(2);
    expect(cheapestId([deal()])).toBeNull();
  });
});

describe("trip labels", () => {
  const base = { origin: "TLV", destination: "Rome", dates_hint: "", depart_from: null, depart_to: null };
  it("route and dates", () => {
    expect(tripRoute(base)).toBe("TLV → Rome");
    expect(tripRoute({ origin: "", destination: "Rome" })).toBe("Rome");
    expect(tripDates({ ...base, dates_hint: " Hanukkah " })).toBe("Hanukkah");
    expect(tripDates(base)).toBe("");
    expect(tripDates({ ...base, depart_from: "2026-12-20", depart_to: "2026-12-27" })).toMatch(/Dec.*–.*Dec/);
  });

  it("price line: paid once booked, else the best with bags", () => {
    const best = { price: 3200, currency: "ILS", kind: "search" as const, label: "x", deal_id: 1 };
    expect(tripPriceLine({ booked: null, best })).toEqual({ label: "Best with bags", price: "₪3,200" });
    const booked = { deal_id: null, airline: "", flight_numbers: "", depart_date: "", return_date: "", price: 4000, currency: "ILS", booking_ref: "", notes: "" };
    expect(tripPriceLine({ booked, best })).toEqual({ label: "Paid", price: "₪4,000" });
    expect(tripPriceLine({ booked: null, best: null })).toBeNull();
  });

  it("sorts planning before booked before done, newest first", () => {
    const t = (id: number, status: Trip["status"], updated_at: string) => ({ id, status, updated_at });
    const sorted = sortTrips([t(1, "done", "2026-10-10"), t(2, "planning", "2026-10-01"), t(3, "booked", "2026-10-12"), t(4, "planning", "2026-10-05")]);
    expect(sorted.map((x) => x.id)).toEqual([4, 2, 3, 1]);
  });
});

describe("refs and pre-fills", () => {
  it("a search is its label without the watch tag", () => {
    expect(searchRef("TLV -> Rome (December) [watch #4]")).toBe("TLV -> Rome (December)");
    expect(searchRef("TLV -> Rome (December)")).toBe("TLV -> Rome (December)");
  });

  it("a new trip from a deal takes its route, party, month and currency", () => {
    const fields = tripFromDeal(deal({ compare_currency: "ILS" }));
    expect(fields).toMatchObject({ origin: "TLV", destination: "Rome", currency: "ILS", passengers: { adults: 2, children: 2, infants: 0 } });
    expect(fields.name).toMatch(/^Rome · Dec/);
  });

  it("a new trip from a watch takes its criteria", () => {
    const w = { origin: "TLV", destination: "Athens", dates_hint: "Sukkot", depart_from: "2026-10-01", depart_to: null, passengers: { adults: 2, children: 1, infants: 0 }, currency: "EUR" } as unknown as Watch;
    expect(tripFromWatch(w)).toMatchObject({ name: "Athens", dates_hint: "Sukkot", depart_from: "2026-10-01", currency: "EUR" });
  });

  it("the booked form pre-fills the price with bags", () => {
    const b = bookedFromDeal(deal({ id: 7, bags: bags(800, 900, 1000) }));
    expect(b).toMatchObject({ deal_id: 7, airline: "Wizz Air", flight_numbers: "W6 1234", price: 950, currency: "USD", depart_date: "2026-12-20" });
  });

  it("reads the trip id from the hash", () => {
    expect(tripIdFromHash("#trips/12")).toBe(12);
    expect(tripIdFromHash("#trips")).toBeNull();
    expect(tripIdFromHash("#watches/3")).toBeNull();
  });
});
