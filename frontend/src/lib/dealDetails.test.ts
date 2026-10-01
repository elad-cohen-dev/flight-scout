import { describe, expect, it } from "vitest";
import {
  bagBadges,
  bagCheckoutNote,
  bagSummary,
  bagTotal,
  bagWords,
  canFetch,
  costHint,
  costLine,
  directSeller,
  errorText,
  linkLabel,
  priceIncludesBag,
  selectedSeller,
} from "./dealDetails";
import type { DealDetails, Seller } from "../types";

const seller = (over: Partial<Seller>): Seller => ({
  index: 0,
  book_with: "Kiwi.com",
  airline: false,
  price: 471,
  option_title: "",
  extensions: [],
  carry_on: { text: "", fee: null, max_fee: null },
  checked: { text: "", fee: null, max_fee: null },
  bag_notes: [],
  price_incl_bags: null,
  price_incl_bags_max: null,
  bags_verified: false,
  separate_tickets: false,
  phone: "",
  links: [{ part: "together", label: "Kiwi.com", url: "/api/deals/1/book/0?part=together" }],
  ...over,
});

const details = (sellers: Seller[]): DealDetails => ({
  deal_id: 1,
  status: "ready",
  message: "",
  cost: 0,
  left: 223,
  budget: 230,
  spent: 2,
  fetched_at: "",
  currency: "USD",
  outbound: null,
  return_leg: null,
  sellers,
  bags_requested: [],
  verified_incl_bags: false,
  price_incl_bags: null,
});

describe("deal details", () => {
  it("phrases the cost", () => {
    expect(costHint(2)).toBe("uses 1–2 live searches");
    expect(costHint(1)).toBe("uses 1 live search");
    expect(costLine({ cost: 2, left: 223 })).toBe("Uses 2 of your 223 live searches left");
    expect(costLine({ cost: 1, left: 1 })).toBe("Uses 1 of your 1 live search left");
    expect(costLine({ cost: 0, left: 5 })).toMatch(/^Free/);
  });

  it("only offers a fetch the budget covers", () => {
    expect(canFetch({ status: "needs_fetch", cost: 2, left: 2, message: "" })).toBe(true);
    expect(canFetch({ status: "needs_fetch", cost: 2, left: 1, message: "Needs 2 live searches; 1 left" })).toBe(false);
    expect(canFetch({ status: "ready", cost: 0, left: 9, message: "" })).toBe(false);
  });

  it("picks the cheapest airline on one ticket as the direct seller", () => {
    const split = seller({
      book_with: "Smartwings",
      airline: true,
      price: 400,
      separate_tickets: true,
      links: [
        { part: "departing", label: "Smartwings", url: "/a" },
        { part: "returning", label: "Smartwings", url: "/b" },
      ],
    });
    const direct = seller({ book_with: "Smartwings", airline: true, price: 486, links: [{ part: "together", label: "Smartwings", url: "/c" }] });
    expect(directSeller(details([seller({}), split, direct]))?.link.url).toBe("/c");
    expect(directSeller(details([seller({})]))).toBeNull();
    expect(directSeller(null)).toBeNull();
    expect(linkLabel(split, split.links[1])).toBe("Return: Smartwings");
    expect(linkLabel(direct, direct.links[0])).toBe("Book with Smartwings");
  });

  it("summarises bag fees", () => {
    const s = seller({ carry_on: { text: "1 free carry-on", fee: 0, max_fee: 0 }, checked: { text: "1st checked bag: 35", fee: 35, max_fee: 35 } });
    expect(bagSummary(s, "USD")).toEqual(["Carry-on free", "Checked bag $35/traveller"]);
    const range = seller({ checked: { text: "1st checked bag: 42-60", fee: null, max_fee: 60 } });
    expect(bagSummary(range, "USD")).toEqual(["Checked bag 42-60"]);
  });

  it("extracts the API's error detail", () => {
    expect(errorText(new Error('409: {"detail":"Needs 2 live searches; only 1 left"}'))).toBe("Needs 2 live searches; only 1 left");
    expect(errorText(new Error("502: Bad Gateway"))).toBe("502: Bad Gateway");
  });
});

describe("bags on the card", () => {
  const wizz = seller({
    book_with: "Wizz Air",
    airline: true,
    price: 324,
    carry_on: { text: "1st carry-on bag: 29", fee: 29, max_fee: 29 },
    checked: { text: "1st checked bag: 42", fee: 42, max_fee: 42 },
    price_incl_bags: 366,
    price_incl_bags_max: 366,
    bags_verified: true,
    links: [{ part: "together", label: "Wizz Air", url: "/w" }],
  });
  const kiwi = seller({ price: 311, checked: { text: "1st checked bag: 38-55", fee: null, max_fee: 55 }, price_incl_bags_max: 366 });
  const fetched = { ...details([wizz, kiwi]), bags_requested: ["checked" as const] };

  it("says 'incl.' only when the source's price includes the bag", () => {
    expect(priceIncludesBag("google_flights", "checked")).toBe(false);
    expect(priceIncludesBag("google_flights", "carry_on")).toBe(true);
    expect(priceIncludesBag("aviasales", "carry_on")).toBe(false);
    expect(priceIncludesBag("web", "checked")).toBe(true);
    expect(priceIncludesBag("", "checked")).toBe(true);
  });

  it("shows 'not in price' until the exact total is known, then nothing", () => {
    const labels = (src: string, d: DealDetails | null) => bagBadges(src, { checked_bag: true, cabin_bag: true }, d).map((b) => b.label);
    expect(labels("google_flights", null)).toEqual(["Trolley incl.", "20kg bag not in price"]);
    expect(labels("google_flights", fetched)).toEqual(["Trolley incl."]);
    expect(labels("aviasales", null)).toEqual(["Trolley not in price", "20kg bag not in price"]);
    expect(labels("web", null)).toEqual(["Trolley incl.", "20kg bag incl."]);
    // An inexact seller as the selected one doesn't count as known.
    const onlyKiwi = { ...details([kiwi]), bags_requested: ["checked" as const] };
    expect(labels("google_flights", onlyKiwi)).toEqual(["Trolley incl.", "20kg bag not in price"]);
  });

  it("takes the bag total from the seller the primary button books", () => {
    expect(selectedSeller(fetched)?.book_with).toBe("Wizz Air");
    expect(bagTotal(fetched)).toBe(366);
    expect(bagTotal({ ...details([kiwi]), bags_requested: ["checked"] })).toBeNull();
    expect(bagTotal({ ...fetched, status: "expired" })).toBeNull();
    expect(bagWords(["carry_on", "checked"])).toBe("trolley + 20kg bag");
  });

  it("phrases the checkout step with the exact fee", () => {
    expect(bagCheckoutNote(wizz, ["checked"], "USD")).toBe("Add the 20kg bag at Wizz Air checkout: $42 per traveller");
    expect(bagCheckoutNote(seller({ book_with: "El Al", checked: { text: "1 free checked bag", fee: 0, max_fee: 0 } }), ["checked"], "USD"))
      .toBe("El Al includes the 20kg bag free");
    expect(bagCheckoutNote(kiwi, ["checked"], "USD")).toBeNull();
    expect(bagCheckoutNote(wizz, [], "USD")).toBeNull();
  });
});
