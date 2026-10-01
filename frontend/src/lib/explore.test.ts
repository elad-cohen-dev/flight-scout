import { describe, expect, it } from "vitest";
import type { ExploreDestination } from "../types";
import {
  costLine,
  DEFAULT_EXPLORE,
  destinationWatchRequest,
  exploreRequest,
  filterDestinations,
  liveSearchRequest,
  markerLabel,
  monthOptions,
  regionCounts,
  validMonth,
} from "./explore";

const dest = (over: Partial<ExploreDestination> = {}): ExploreDestination => ({
  city: "London",
  country: "United Kingdom",
  country_code: "GB",
  region: "europe",
  destination_id: "/m/04jpl",
  airport: "LTN",
  search_destination: "London [LON]",
  lat: 51.5,
  lon: -0.12,
  thumbnail: "",
  price: 401,
  price_per_person: 401,
  currency: "USD",
  depart_date: "2026-11-17",
  return_date: "2026-11-24",
  return_estimated: false,
  nights: 7,
  airline: "Wizz Air",
  airline_code: "W6",
  stops: 0,
  duration: "5h 30m",
  duration_minutes: 330,
  source: "google_explore",
  price_confidence: "estimated",
  google_url: "",
  booking_links: [],
  ...over,
});

const OCT1 = new Date(2026, 9, 1);

describe("explore criteria", () => {
  it("offers the six months Google Explore answers", () => {
    expect(monthOptions(OCT1).map((m) => m.value)).toEqual([10, 11, 12, 1, 2, 3]);
    expect(monthOptions(OCT1)[3].label).toMatch(/Jan.*27/);
    expect(validMonth(11, OCT1)).toBe(11);
    expect(validMonth(6, OCT1)).toBe(0); // dropped out of the horizon
  });

  it("sends only what Google prices on — the price cap and region stay client-side", () => {
    const req = exploreRequest({ ...DEFAULT_EXPLORE, direct_only: true, max_price: "300", region: "asia", month: 12 }, OCT1);
    expect(req).toMatchObject({ origin: "TLV", month: 12, max_stops: 0, travel_duration: "week", trip_type: "round_trip" });
    expect(req).not.toHaveProperty("max_price");
    expect(req).not.toHaveProperty("region");
    // ...so changing them keeps the same cache key
    expect(JSON.stringify(exploreRequest({ ...DEFAULT_EXPLORE, max_price: "1" }, OCT1))).toBe(JSON.stringify(exploreRequest(DEFAULT_EXPLORE, OCT1)));
  });

  it("filters by region and party-total price for free", () => {
    const list = [dest({ city: "Paphos", region: "europe", price: 89 }), dest({ city: "Bangkok", region: "asia", price: 689 }), dest()];
    expect(filterDestinations(list, { max_price: "400", region: "" }).map((d) => d.city)).toEqual(["Paphos"]);
    expect(filterDestinations(list, { max_price: "", region: "asia" }).map((d) => d.city)).toEqual(["Bangkok"]);
    expect(regionCounts(list)).toEqual([
      { region: "europe", count: 2 },
      { region: "asia", count: 1 },
    ]);
  });

  it("states the cost before running", () => {
    expect(costLine({ configured: true, cached: false, cost: 1, left: 225, budget: 230 })).toBe("1 live search · 225 left this month");
    expect(costLine({ configured: true, cached: true, cost: 0, left: 225, budget: 230 })).toBe("Cached — free · 225 left this month");
    expect(costLine({ configured: true, cached: false, cost: 1, left: 0, budget: 230 })).toMatch(/No live searches left/);
    expect(costLine({ configured: false, cached: false, cost: 0, left: 0, budget: 230 })).toBeNull();
  });

  it("labels markers compactly", () => {
    expect(markerLabel(89, "USD")).toBe("$89");
    expect(markerLabel(1240, "EUR")).toBe("€1.2k");
    expect(markerLabel(2000, "ILS")).toBe("₪2k");
    expect(markerLabel(512, "CHF")).toBe("512 CHF");
  });
});

describe("from a destination", () => {
  const form = { ...DEFAULT_EXPLORE, passengers: { adults: 2, children: 0, infants: 0 }, direct_only: true, cabin_bag: true };

  it("checks exactly Explore's dates live, on every airport of the city", () => {
    const req = liveSearchRequest(dest(), form);
    expect(req).toMatchObject({
      origin: "TLV",
      destination: "London [LON]",
      dates_hint: "specific dates",
      trip_type: "round_trip",
      depart_from: "2026-11-17",
      depart_to: "2026-11-17",
      nights_min: 7,
      nights_max: 7,
      max_stops: 0,
      cabin_bag: true,
      passengers: { adults: 2, children: 0, infants: 0 },
      currency: "USD",
    });
  });

  it("one-way checks have no return", () => {
    const req = liveSearchRequest(dest({ return_date: "", nights: null }), { ...form, trip_type: "one_way" });
    expect(req).toMatchObject({ trip_type: "one_way", nights_min: null, nights_max: null });
  });

  it("watches the departure month with a trip length around the chosen one", () => {
    const w = destinationWatchRequest(dest({ depart_date: "2027-01-13" }), { ...form, travel_duration: "weekend" });
    expect(w).toMatchObject({ destination: "London [LON]", dates_hint: "January 2027", depart_from: null, nights_min: 2, nights_max: 3, max_stops: 0 });
  });
});
