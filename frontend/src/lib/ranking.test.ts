import { describe, expect, it } from "vitest";
import { isUnconfirmed, rankDeals } from "./ranking";
import type { StoredDeal } from "../types";

const deal = (id: number, price: number, source: StoredDeal["source"], depart = "2026-11-17", duration = "5h 00m") =>
  ({ id, price, currency: "USD", source, depart_date: depart, depart_time: "", duration, compare_price: price, compare_currency: "USD" }) as StoredDeal;

describe("deal ranking", () => {
  const explore = deal(1, 412, "google_explore", "2026-10-20", "5h 35m");
  const live = deal(2, 486, "google_flights", "2026-11-17", "4h 15m");
  const pricier = deal(3, 702, "google_flights", "2026-11-17", "4h 25m");

  it("puts confirmed fares ahead of a cheaper Explore estimate and gives them the ribbon", () => {
    const r = rankDeals([explore, pricier, live], "price");
    expect(r.confirmed.map((d) => d.id)).toEqual([2, 3]);
    expect(r.estimates.map((d) => d.id)).toEqual([1]);
    expect(r.cheapestId).toBe(2);
  });

  it("keeps estimates after confirmed fares in every sort", () => {
    expect(rankDeals([live, explore], "depart").confirmed.map((d) => d.id)).toEqual([2]);
    expect(rankDeals([pricier, live, explore], "duration").confirmed.map((d) => d.id)).toEqual([2, 3]);
    expect(rankDeals([live, explore], "duration").cheapestId).toBeNull(); // ribbon only when sorted by price
  });

  it("never puts the ribbon on an estimate", () => {
    const cached = deal(4, 300, "aviasales");
    const r = rankDeals([explore, cached], "price");
    expect(r.confirmed).toEqual([]);
    expect(r.estimates.map((d) => d.id)).toEqual([4, 1]);
    expect(r.cheapestId).toBeNull();
  });

  it("treats web and legacy rows as confirmed-tier, Explore and Aviasales as estimates", () => {
    expect([deal(5, 1, "web"), deal(6, 1, ""), deal(7, 1, "google_flights")].map(isUnconfirmed)).toEqual([false, false, false]);
    expect([explore, deal(8, 1, "aviasales")].map(isUnconfirmed)).toEqual([true, true]);
  });
});

describe("watch wording and live checks", async () => {
  const { describeWatch } = await import("./watches");
  const { checkLiveRequest } = await import("./search");

  it("states the bag as a requirement, not as included in the price", () => {
    const w = { origin: "TLV", destination: "London", dates_hint: "November", max_stops: 0, cabin_bag: false, checked_bag: true, currency: "USD" };
    const { search } = describeWatch(w as Parameters<typeof describeWatch>[0]);
    expect(search).toContain("needs a 20kg checked bag");
    expect(search).not.toContain("included in the price");
  });

  it("narrows an estimate's search to its exact date pair", () => {
    const params = {
      trip_type: "round_trip", passengers: { adults: 1, children: 0, infants: 0 }, cabin_class: "economy", max_stops: 0,
      cabin_bag: false, checked_bag: true, depart_from: "2026-10-12", depart_to: "2026-11-30", nights_min: 5, nights_max: 9,
      max_price: null, currency: "USD", nearby_airports: false,
    };
    const d = {
      ...deal(9, 412, "google_explore", "2026-10-20"), return_date: "2026-10-27", origin: "TLV", destination: "STN",
      search_query: "TLV -> London (October - November after October 11th, direct only, checked 20kg bag)", search_params: params,
    } as unknown as StoredDeal;
    const req = checkLiveRequest(d)!;
    expect([req.origin, req.destination, req.dates_hint]).toEqual(["TLV", "London", "2026-10-20 to 2026-10-27"]);
    expect([req.depart_from, req.depart_to, req.nights_min, req.max_stops, req.checked_bag]).toEqual([null, null, null, 0, true]);
    expect(checkLiveRequest({ ...d, depart_date: "late October" })).toBeNull();
  });
});
