import { describe, expect, it } from "vitest";
import {
  approx,
  asOfLabel,
  asQuoted,
  bagCountWords,
  bagsAdded,
  basisLabel,
  checkoutPreview,
  freshness,
  lineAmount,
  lineLabel,
  recheckText,
  sellerEstimate,
  totalRange,
} from "./bags";
import { bagBadges } from "./dealDetails";
import { comparable } from "./format";
import { groupHistory } from "./history";
import { rankDeals } from "./ranking";
import {
  checkedBagCount,
  criteriaChips,
  DEFAULT_FORM,
  formToRequest,
  maxCheckedBags,
  parseSearchQuery,
  requestToForm,
  rerunRequest,
} from "./search";
import { criteriaKey, describeWatch, formToWatchUpdate, watchToForm } from "./watches";
import type { BagLine, BagPricing, StoredDeal, Watch } from "../types";

const checked: BagLine = { kind: "checked", count: 2, directions: 2, low: 360, high: 1000, basis: "table", carrier: "Wizz Air" };
const trolley: BagLine = { kind: "trolley", count: 2, directions: 2, low: 0, high: 0, basis: "google", carrier: "Wizz Air" };
const WIZZ: BagPricing = {
  basis: "table",
  fare: 2140,
  total: 2820,
  total_min: 2500,
  total_max: 3140,
  currency: "ILS",
  lines: [trolley, checked],
  added_at_checkout: true,
  as_of: "2026-10",
};

describe("price with bags wording", () => {
  it("knows when bag fees were added on top of the fare", () => {
    expect(bagsAdded(WIZZ)).toBe(true);
    expect(bagsAdded({ ...WIZZ, basis: "none", lines: [], total: 2140, total_min: 2140, total_max: 2140 })).toBe(false);
    expect(bagsAdded({ ...WIZZ, basis: "as_quoted" })).toBe(false);
    expect(bagsAdded({ ...WIZZ, basis: "included", lines: [trolley], total: 2140, total_min: 2140, total_max: 2140 })).toBe(false);
    expect(bagsAdded(null)).toBe(false);
    expect(asQuoted({ ...WIZZ, basis: "as_quoted" })).toBe(true);
  });

  it("names the bags, the lines and where each fee came from", () => {
    expect(bagCountWords(WIZZ)).toBe("2 trolleys + 2 checked bags");
    expect(lineLabel(checked)).toBe("2 checked bags × 2 ways");
    expect(lineLabel({ ...checked, count: 1, directions: 1 })).toBe("1 checked bag, one way");
    expect(basisLabel(checked, WIZZ.as_of)).toBe("fee table, Oct 2026");
    expect(basisLabel(trolley, "")).toBe("Google added it");
    expect(basisLabel({ ...checked, basis: "seller" }, "")).toBe("seller's fee");
    expect(basisLabel({ ...checked, basis: "typical" }, "")).toBe("typical range — airline not in table");
    expect(basisLabel({ ...checked, basis: "included" }, "")).toBe("included in fare");
    expect(asOfLabel("garbage")).toBe("");
  });

  it("shows ranges in the breakdown and an exact fee as one price", () => {
    expect(lineAmount(checked, "ILS")).toBe("₪360–1,000");
    expect(lineAmount({ ...checked, low: 240, high: 240 }, "ILS")).toBe("₪240");
    expect(lineAmount(trolley, "ILS")).toBe("in fare");
    expect(lineAmount({ ...checked, basis: "included", low: 0, high: 0 }, "ILS")).toBe("free");
    expect(totalRange(WIZZ)).toBe("₪2,500–3,140");
    expect(approx(WIZZ)).toBe("≈ ");
    expect(approx({ ...WIZZ, total_min: 2820, total_max: 2820 })).toBe("");
  });

  it("previews the checkout when bags are added there", () => {
    expect(checkoutPreview({ airline: "Wizz Air", bags: WIZZ })).toBe(
      "Wizz Air shows about ₪2,140 — bags are added during checkout, ≈ ₪2,820 total",
    );
    expect(checkoutPreview({ airline: "El Al", bags: { ...WIZZ, added_at_checkout: false } })).toBeNull();
    expect(checkoutPreview({ airline: "X", bags: null })).toBeNull();
  });

  it("estimates a seller's total with the deal's bag fees when the seller states none", () => {
    expect(sellerEstimate(2100, "ILS", WIZZ)).toBe(2780);
    expect(sellerEstimate(2100, "USD", WIZZ)).toBeNull(); // different currency: no guess
    expect(sellerEstimate(null, "ILS", WIZZ)).toBeNull();
  });
});

describe("freshness and recheck", () => {
  const now = new Date("2026-10-14T12:00:00Z").getTime();
  it("says when the price was seen", () => {
    expect(freshness({ seen_at: "2026-10-14T09:00:00Z", found_at: "2026-10-13T09:00:00Z" }, now)).toBe("checked 3h ago");
    expect(freshness({ seen_at: "", found_at: "2026-10-12T12:00:00Z" }, now)).toBe("checked 2d ago");
    expect(freshness({ seen_at: "", found_at: "" }, now)).toBe("");
  });

  it("words a recheck", () => {
    const base = { currency: "ILS", old_seen_at: "2026-10-14T09:00:00Z" };
    expect(recheckText({ ...base, status: "cheaper", delta: -120 }, now)).toBe("↓ ₪120 since 3h ago");
    expect(recheckText({ ...base, status: "pricier", delta: 40 }, now)).toBe("↑ ₪40 since 3h ago");
    expect(recheckText({ ...base, status: "same", delta: 0 }, now)).toBe("Same price");
    expect(recheckText({ ...base, status: "gone", delta: null }, now)).toBe("No longer available");
  });
});

const deal = (id: number, price: number, extra: Partial<StoredDeal> = {}) =>
  ({
    id,
    price,
    currency: "ILS",
    source: "google_flights",
    depart_date: "2026-11-17",
    depart_time: "",
    duration: "4h",
    compare_price: price,
    compare_currency: "ILS",
    search_query: "TLV -> Budapest (November)",
    found_at: "2026-10-14T09:00:00Z",
    airline: "",
    origin: "TLV",
    destination: "BUD",
    return_date: "",
    times_seen: 1,
    stops: 0,
    ...extra,
  }) as StoredDeal;

describe("sorting by the price with bags", () => {
  // Wizz's bare fare is cheaper, but with two checked bags El Al wins.
  const wizz = deal(1, 2140, { airline: "Wizz Air", compare_total: 2820, bags: WIZZ });
  const elal = deal(2, 2600, { airline: "El Al", compare_total: 2600 });

  it("comparable() uses compare_total, then compare_price, then bags.total", () => {
    expect(comparable(wizz)).toEqual({ price: 2820, currency: "ILS" });
    expect(comparable({ price: 100, currency: "USD", compare_price: 300, compare_currency: "ILS" })).toEqual({ price: 300, currency: "ILS" });
    expect(comparable({ price: 100, currency: "USD", bags: { total: 140 } })).toEqual({ price: 140, currency: "USD" });
  });

  it("ranks and picks the cheapest by it", () => {
    const r = rankDeals([wizz, elal], "price");
    expect(r.confirmed.map((d) => d.id)).toEqual([2, 1]);
    expect(r.cheapestId).toBe(2);
  });

  it("History's run minimum is the price with bags", () => {
    const [g] = groupHistory([wizz, elal]);
    expect(g.latest.minPrice).toBe(2600);
    expect(g.latest.deals.map((d) => d.id)).toEqual([2, 1]);
  });
});

describe("checked-bag count in requests, labels and watches", () => {
  const family = { adults: 2, children: 2, infants: 1 };

  it("allows up to two per seated traveller", () => {
    expect(maxCheckedBags(family)).toBe(8);
    expect(maxCheckedBags({ adults: 1, children: 0, infants: 0 })).toBe(2);
  });

  it("reads the legacy flag as one per seated traveller", () => {
    expect(checkedBagCount({ checked_bag: true, passengers: family })).toBe(4);
    expect(checkedBagCount({ checked_bags: 3, checked_bag: true, passengers: family })).toBe(3);
    expect(checkedBagCount({ checked_bag: false })).toBe(0);
  });

  it("sends the count plus the legacy flag, clamped to the party", () => {
    const req = formToRequest({ ...DEFAULT_FORM, destination: "BUD", passengers: family, checked_bags: 3 });
    expect([req.checked_bags, req.checked_bag]).toEqual([3, true]);
    const none = formToRequest({ ...DEFAULT_FORM, destination: "BUD", checked_bags: 0 });
    expect([none.checked_bags, none.checked_bag]).toEqual([0, false]);
    expect(formToRequest({ ...DEFAULT_FORM, destination: "BUD", checked_bags: 9 }).checked_bags).toBe(2);
  });

  it("round-trips a request through the form (re-run from History)", () => {
    const old = { origin: "TLV", destination: "Rome", dates_hint: "November", cabin_bag: false, checked_bag: true, passengers: family };
    expect(requestToForm(old).checked_bags).toBe(4);
    expect(requestToForm({ ...old, checked_bags: 2 }).checked_bags).toBe(2);
  });

  it("parses the backend's '3 checked bags' label flag", () => {
    const p = parseSearchQuery("TLV -> Budapest (November, 2 adults, 3 checked bags, checked 20kg bag) [watch #4]");
    expect(p).toMatchObject({ dates_hint: "November", checked_bag: true, checked_bags: 3, watchId: 4 });
    const legacy = parseSearchQuery("TLV -> Prague (October, cabin bag, checked 20kg bag)");
    expect(legacy).toMatchObject({ dates_hint: "October", checked_bag: true, checked_bags: null, cabin_bag: true });
    const req = rerunRequest(deal(9, 100, { search_query: "TLV -> Budapest (November, 3 checked bags, checked 20kg bag)", search_params: null }));
    expect(req?.checked_bags).toBe(3);
  });

  it("chips, watch words and the criteria key use the count", () => {
    expect(criteriaChips({ checked_bags: 3, checked_bag: true })).toContain("3 checked bags");
    expect(criteriaChips({ checked_bag: true, passengers: family })).toContain("4 checked bags");
    const words = describeWatch({ origin: "TLV", destination: "BUD", dates_hint: "May", checked_bags: 2, checked_bag: true });
    expect(words.search).toContain("needs 2 checked 20kg bags");
    const key = (n: number) => criteriaKey({ origin: "TLV", destination: "BUD", dates_hint: "May", checked_bags: n, checked_bag: n > 0 });
    expect(key(2)).not.toBe(key(3));
    // A legacy flag for one adult is the same search as one checked bag.
    expect(criteriaKey({ origin: "TLV", destination: "BUD", dates_hint: "May", checked_bag: true })).toBe(key(1));
  });

  it("the watch editor patches checked_bags", () => {
    const w = { origin: "TLV", destination: "BUD", dates_hint: "May", max_price: 3000, currency: "ILS", checked_bags: 3, checked_bag: true, passengers: family } as Watch;
    const form = watchToForm(w);
    expect(form.checked_bags).toBe(3);
    const patch = formToWatchUpdate({ ...form, checked_bags: 0 });
    expect([patch?.checked_bags, patch?.checked_bag]).toEqual([0, false]);
  });
});

describe("bag badges with the price with bags", () => {
  it("says a bag was added (estimate) instead of 'not in price'", () => {
    const labels = bagBadges("google_flights", { cabin_bag: true, checked_bag: true }, null, WIZZ).map((b) => b.label);
    expect(labels).toEqual(["Trolley incl.", "20kg bag added"]);
    // Pre-3.0 server (no pricing): unchanged.
    expect(bagBadges("google_flights", { checked_bag: true }, null).map((b) => b.label)).toEqual(["20kg bag not in price"]);
  });
});
