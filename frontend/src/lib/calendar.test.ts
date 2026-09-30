import { describe, expect, it } from "vitest";
import type { SearchParams, StoredDeal } from "../types";
import { addDays, aroundDateRequest, buildCalendar, monthGrid, monthRequest, pickBest, routeKey, routeVariants, runPoints, STALE_DAYS, tierOf } from "./calendar";
import type { Sighting } from "./calendar";

const NOW = new Date("2026-09-30T12:00:00Z");
const daysAgo = (n: number) => new Date(NOW.getTime() - n * 86_400_000).toISOString();
let nextId = 1;

const PARAMS: SearchParams = {
  trip_type: "round_trip",
  passengers: { adults: 1, children: 0, infants: 0 },
  cabin_class: "economy",
  max_stops: null,
  cabin_bag: false,
  checked_bag: false,
  depart_from: null,
  depart_to: null,
  nights_min: null,
  nights_max: null,
  max_price: null,
  currency: "USD",
  nearby_airports: false,
};

function deal(over: Partial<StoredDeal> & { ago?: number } = {}): StoredDeal {
  const { ago = 1, ...rest } = over;
  return {
    id: nextId++,
    search_query: "TLV -> Prague (October)",
    route_key: "",
    found_at: daysAgo(ago),
    times_seen: 1,
    first_found_at: null,
    origin: "TLV",
    destination: "Prague",
    airline: "Wizz Air",
    price: 300,
    currency: "USD",
    depart_date: "2026-10-12",
    return_date: "2026-10-16",
    duration: "4h",
    stops: 0,
    booking_url: "",
    source_url: "",
    notes: "",
    origin_airport: "TLV",
    destination_airport: "PRG",
    depart_time: "",
    arrive_time: "",
    return_depart_time: "",
    flight_numbers: "",
    airline_code: "W6",
    price_per_person: null,
    price_confidence: "verified",
    booking_links: [],
    search_params: PARAMS,
    compare_price: rest.price ?? 300,
    compare_currency: "USD",
    ...rest,
  };
}

const ROUTE = "TLV -> Prague";
const build = (deals: StoredDeal[], directOnly = false, variant: string | null = null) =>
  buildCalendar(deals, ROUTE, { variant, directOnly }, NOW);

describe("routeKey", () => {
  it("drops autocomplete codes so 'Prague [PRG]' reads like 'Prague'", () => {
    expect(routeKey("Tel Aviv [TLV] -> Prague [PRG] (October)")).toBe("Tel Aviv -> Prague");
  });

  it("matches the backend's route grouping", () => {
    expect(routeKey("TLV -> London (anytime in October, cabin bag) [watch #4]")).toBe("TLV -> London");
    expect(routeKey("TLV -> Prague (depart 2026-10-01 to 2026-10-09)")).toBe("TLV -> Prague");
  });
});

describe("date helpers", () => {
  it("adds days across month and year ends", () => {
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-12-30", 3)).toBe("2027-01-02");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("lays a month out in Monday-first weeks", () => {
    const weeks = monthGrid("2026-10"); // Oct 1 2026 is a Thursday
    expect(weeks[0]).toEqual([null, null, null, "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]);
    expect(weeks.flat().filter(Boolean)).toHaveLength(31);
    expect(weeks.every((w) => w.length === 7)).toBe(true);
  });
});

describe("pickBest (staleness rule)", () => {
  const s = (price: number, ageDays: number, foundAt = daysAgo(ageDays)): Sighting => ({
    deal: deal({ price }),
    price,
    foundAt,
    ageDays,
    stale: ageDays >= STALE_DAYS,
  });

  it("prefers the cheapest fresh sighting over a cheaper stale one", () => {
    const { best, staleLow } = pickBest([s(250, 40), s(320, 2), s(300, 10)]);
    expect(best.price).toBe(300);
    expect(staleLow?.price).toBe(250);
  });

  it("falls back to the cheapest stale sighting when nothing is fresh", () => {
    const { best, staleLow } = pickBest([s(280, 45), s(260, 60)]);
    expect(best.price).toBe(260);
    expect(best.stale).toBe(true);
    expect(staleLow).toBeNull();
  });

  it("breaks price ties with the most recent sighting", () => {
    const { best } = pickBest([s(300, 5), s(300, 1)]);
    expect(best.ageDays).toBe(1);
  });

  it("treats exactly STALE_DAYS old as stale", () => {
    expect(s(1, STALE_DAYS).stale).toBe(true);
    expect(s(1, STALE_DAYS - 0.01).stale).toBe(false);
  });
});

describe("buildCalendar", () => {
  it("puts only ISO-dated upcoming deals on the grid and counts the rest", () => {
    const cal = build([
      deal({ depart_date: "2026-10-12" }),
      deal({ depart_date: "mid October" }),
      deal({ depart_date: "2026-09-01" }), // already departed
      deal({ search_query: "TLV -> London (October)", depart_date: "2026-10-13" }), // other route
    ]);
    expect([...cal.days.keys()]).toEqual(["2026-10-12"]);
    expect(cal.undated).toBe(1);
    expect(cal.past).toBe(1);
    expect(cal.months).toEqual(["2026-10"]);
  });

  it("merges runs of the same route regardless of the label's other criteria", () => {
    const cal = build([
      deal({ search_query: "TLV -> Prague (October)", depart_date: "2026-10-12" }),
      deal({ search_query: "TLV -> Prague (after Oct 11, direct only) [watch #2]", depart_date: "2026-11-02" }),
    ]);
    expect(cal.months).toEqual(["2026-10", "2026-11"]);
  });

  it("ranks by comparable price, not the raw quote", () => {
    const cal = build([
      deal({ price: 1100, currency: "ILS", compare_price: 297, depart_date: "2026-10-12" }),
      deal({ price: 310, airline: "El Al", depart_date: "2026-10-12" }),
    ]);
    const day = cal.days.get("2026-10-12")!;
    expect(day.best.deal.currency).toBe("ILS");
    expect(day.best.price).toBe(297);
  });

  it("applies the fresh-first rule per day and marks stale days", () => {
    const cal = build([
      deal({ price: 250, ago: 40, airline: "A", depart_date: "2026-10-12" }),
      deal({ price: 320, ago: 2, airline: "B", depart_date: "2026-10-12" }),
      deal({ price: 200, ago: 45, depart_date: "2026-10-20" }),
    ]);
    const d12 = cal.days.get("2026-10-12")!;
    expect(d12.best.price).toBe(320);
    expect(d12.stale).toBe(false);
    expect(d12.staleLow?.price).toBe(250);
    expect(d12.fares.map((f) => f.price)).toEqual([320, 250]); // fresh first
    expect(cal.days.get("2026-10-20")!.stale).toBe(true);
    // The cheapest mark ignores the stale-only $200 day while fresh data exists.
    expect(cal.cheapestDates).toEqual(["2026-10-12"]);
    expect(cal.min).toBe(200);
    expect(cal.max).toBe(320);
  });

  it("counts a fare re-found by several runs once, keeping its latest sighting", () => {
    const cal = build([deal({ ago: 1 }), deal({ ago: 3 }), deal({ ago: 35 })]);
    const day = cal.days.get("2026-10-12")!;
    expect(day.fares).toHaveLength(1);
    expect(day.best.ageDays).toBeCloseTo(1);
    expect(day.nights).toBe(4);
  });

  it("filters direct-only and keeps party sizes apart", () => {
    const duo = { ...PARAMS, passengers: { adults: 2, children: 0, infants: 0 } };
    const deals = [
      deal({ price: 280, stops: 1, depart_date: "2026-10-12", ago: 1 }),
      deal({ price: 330, stops: 0, depart_date: "2026-10-12", airline: "El Al", ago: 1 }),
      deal({ price: 560, search_params: duo, depart_date: "2026-10-14", ago: 0.5 }),
    ];
    // Latest sighting is the 2-traveller search, so that kind is shown by default.
    const byDefault = build(deals);
    expect(byDefault.variant?.party).toBe(2);
    expect([...byDefault.days.keys()]).toEqual(["2026-10-14"]);
    expect(byDefault.variants).toHaveLength(2);

    const solo = byDefault.variants.find((v) => v.party === 1)!.key;
    expect(build(deals, false, solo).days.get("2026-10-12")!.best.price).toBe(280);
    expect(build(deals, true, solo).days.get("2026-10-12")!.best.price).toBe(330);
  });

  it("ranks within the dominant comparison currency and counts the others", () => {
    const cal = build([
      deal({ depart_date: "2026-10-12" }),
      deal({ depart_date: "2026-10-13", airline: "X" }),
      deal({ depart_date: "2026-10-14", compare_currency: "EUR", compare_price: 100 }),
    ]);
    expect(cal.currency).toBe("USD");
    expect(cal.otherCurrency).toBe(1);
    expect(cal.days.has("2026-10-14")).toBe(false);
  });
});

describe("tierOf", () => {
  it("splits the route's range into thirds", () => {
    expect(tierOf(100, 100, 400)).toBe("low");
    expect(tierOf(250, 100, 400)).toBe("mid");
    expect(tierOf(400, 100, 400)).toBe("high");
    expect(tierOf(300, 300, 300)).toBe("low");
  });
});

describe("search pre-fill", () => {
  it("searches ±2 days around a date with the route's latest criteria", () => {
    const latest = deal({ search_params: { ...PARAMS, max_stops: 0, nights_min: 3, nights_max: 5 } });
    const req = aroundDateRequest(latest, "2026-10-12", NOW)!;
    expect(req.origin).toBe("TLV");
    expect(req.destination).toBe("Prague");
    expect(req.depart_from).toBe("2026-10-10");
    expect(req.depart_to).toBe("2026-10-14");
    expect(req.max_stops).toBe(0);
    expect(req.nights_min).toBe(3);
  });

  it("never starts a window in the past", () => {
    expect(aroundDateRequest(deal(), "2026-10-01", NOW)!.depart_from).toBe("2026-09-30");
    const month = monthRequest(deal(), "2026-09", NOW)!;
    expect([month.depart_from, month.depart_to]).toEqual(["2026-09-30", "2026-09-30"]);
    const nov = monthRequest(deal(), "2026-11", NOW)!;
    expect([nov.depart_from, nov.depart_to]).toEqual(["2026-11-01", "2026-11-30"]);
  });
});


describe("runPoints", () => {
  it("charts one kind of fare so party totals never mix", () => {
    const duo = { ...PARAMS, passengers: { adults: 2, children: 0, infants: 0 } };
    const deals = [
      deal({ price: 100, compare_price: 100, compare_currency: "USD", search_params: PARAMS, ago: 5 }),
      deal({ price: 90, compare_price: 90, compare_currency: "USD", search_params: PARAMS, ago: 3 }),
      deal({ price: 600, compare_price: 600, compare_currency: "USD", search_params: duo, ago: 1 }),
    ];
    const route = "TLV -> Prague";
    const variants = routeVariants(deals, route);
    expect(variants.map((v) => v.party)).toEqual([2, 1]); // latest first
    expect(runPoints(deals, route, variants[1].key).points.map((p) => p.price)).toEqual([100, 90]);
    expect(runPoints(deals, route, variants[0].key).points.map((p) => p.price)).toEqual([600]);
  });
});
