import { describe, expect, it } from "vitest";
import { bagSummary, canFetch, costHint, costLine, directSeller, errorText, linkLabel } from "./dealDetails";
import type { DealDetails, Seller } from "../types";

const seller = (over: Partial<Seller>): Seller => ({
  index: 0,
  book_with: "Kiwi.com",
  airline: false,
  price: 471,
  option_title: "",
  extensions: [],
  carry_on: { text: "", fee: null },
  checked: { text: "", fee: null },
  bag_notes: [],
  price_incl_bags: null,
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
    const s = seller({ carry_on: { text: "1 free carry-on", fee: 0 }, checked: { text: "1st checked bag: 35", fee: 35 } });
    expect(bagSummary(s, "USD")).toEqual(["Carry-on free", "Checked bag $35/traveller"]);
    const range = seller({ checked: { text: "1st checked bag: 42-60", fee: null } });
    expect(bagSummary(range, "USD")).toEqual(["Checked bag 42-60"]);
  });

  it("extracts the API's error detail", () => {
    expect(errorText(new Error('409: {"detail":"Needs 2 live searches; only 1 left"}'))).toBe("Needs 2 live searches; only 1 left");
    expect(errorText(new Error("502: Bad Gateway"))).toBe("502: Bad Gateway");
  });
});
