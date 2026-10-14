import { beforeEach, describe, expect, it } from "vitest";
import { accuracyLine, clearPending, noteBookClick, parseTotal, shouldAsk, takeFor } from "./priceCheck";
import type { PriceCheckSummary } from "../types";

describe("the 'was it the same price?' prompt", () => {
  beforeEach(() => clearPending());
  const T = 1_000_000;

  it("asks on the clicked deal's card after a real visit, once", () => {
    noteBookClick({ dealId: 7, seller: "Wizz Air", shownTotal: 2980, currency: "ILS" }, T);
    expect(takeFor(8, T + 60_000)).toBeNull(); // another card
    expect(takeFor(7, T + 1_000)).toBeNull(); // a quick tab flick
    expect(takeFor(7, T + 60_000)).toMatchObject({ dealId: 7, seller: "Wizz Air", shownTotal: 2980 });
    expect(takeFor(7, T + 61_000)).toBeNull(); // asked already
  });

  it("forgets a click from hours ago", () => {
    noteBookClick({ dealId: 7, seller: "", shownTotal: 1, currency: "USD" }, T);
    expect(shouldAsk({ dealId: 7, seller: "", shownTotal: 1, currency: "USD", at: T }, 7, T + 4 * 3600_000)).toBe(false);
    expect(takeFor(7, T + 4 * 3600_000)).toBeNull();
  });

  it("parses a typed total", () => {
    expect(parseTotal("₪2,980")).toBe(2980);
    expect(parseTotal("2980.50")).toBe(2980.5);
    expect(parseTotal("")).toBeNull();
    expect(parseTotal("abc")).toBeNull();
  });
});

describe("accuracy line", () => {
  const s: PriceCheckSummary = {
    checks: 9,
    same: 7,
    airlines: [
      { airline: "El Al", checks: 5, same: 5, same_pct: 100, median_diff: 0, currency: "ILS", bags_estimated: 0 },
      { airline: "Wizz Air", checks: 4, same: 2, same_pct: 50, median_diff: 15, currency: "EUR", bags_estimated: 4 },
    ],
  };

  it("names the airline that's off the most", () => {
    expect(accuracyLine(s)).toBe("Price checks: 7 of 9 matched; Wizz Air bags off by ~€15 (more at checkout)");
    const cheaper = { ...s, airlines: [{ ...s.airlines[1], median_diff: -20, bags_estimated: 0 }] };
    expect(accuracyLine(cheaper)).toBe("Price checks: 7 of 9 matched; Wizz Air off by ~€20 (less at checkout)");
  });

  it("is quiet before the first check and when all matched", () => {
    expect(accuracyLine({ checks: 0, same: 0, airlines: [] })).toBeNull();
    expect(accuracyLine(null)).toBeNull();
    expect(accuracyLine({ checks: 5, same: 5, airlines: [s.airlines[0]] })).toBe("Price checks: 5 of 5 matched");
  });
});
