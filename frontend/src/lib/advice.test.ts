import { describe, expect, it } from "vitest";
import { adviceTone, parseCreditBudget, planLine, planLow, planTip, showAdvice } from "./advice";
import type { Advice, WatchPlan } from "../types";

const advice = (verdict: Advice["verdict"]): Advice => ({ verdict, label: "x", reason: "r", days_out: 20 });
const plan = (over: Partial<WatchPlan> = {}): WatchPlan => ({
  per_check: 3, interval_days: 3, next_check: "2026-10-16", next_in_days: 2, used: 6, budget: 12,
  budget_default: true, per_month: 12, reason: "45 days out: every 3 days",
  text: "next live check: in 2 days · 6/12 credits this month", ...over,
});

describe("buy or wait", () => {
  it("maps verdicts to tones", () => {
    expect(adviceTone(advice("buy_now"))).toBe("buy");
    expect(adviceTone(advice("good_price"))).toBe("good");
    expect(adviceTone(advice("wait"))).toBe("wait");
    expect(adviceTone(advice("not_enough_data"))).toBe("unknown");
  });

  it("hides 'Not enough data' unless asked", () => {
    expect(showAdvice(advice("buy_now"))).toBe(true);
    expect(showAdvice(advice("not_enough_data"))).toBe(false);
    expect(showAdvice(advice("not_enough_data"), true)).toBe(true);
    expect(showAdvice(null)).toBe(false);
    expect(showAdvice(undefined, true)).toBe(false);
  });
});

describe("credit plan", () => {
  it("shows the plan line", () => {
    expect(planLine(plan())).toBe("Next live check: in 2 days · 6/12 credits this month");
    expect(planLine(plan({ per_check: 0 }))).toBeNull();
    expect(planLine(plan({ text: "" }))).toBeNull();
    expect(planLine(null)).toBeNull();
  });

  it("explains the cadence and the budget", () => {
    expect(planTip(plan())).toContain("Live check 45 days out: every 3 days (3 live searches per check, ~12 this month)");
    expect(planTip(plan())).toContain("fair share");
    expect(planTip(plan({ budget_default: false, per_check: 1 }))).toContain("1 live search per check");
    expect(planTip(plan({ budget_default: false }))).toContain("set in the watch editor");
  });

  it("flags a budget with fewer than two checks left", () => {
    expect(planLow(plan())).toBe(false); // 6 left, 3 a check
    expect(planLow(plan({ used: 7 }))).toBe(true);
  });

  it("parses the editor's budget", () => {
    expect(parseCreditBudget("")).toBeNull();
    expect(parseCreditBudget("  ")).toBeNull();
    expect(parseCreditBudget("12")).toBe(12);
    expect(parseCreditBudget("0")).toBe(0);
    expect(parseCreditBudget("-1")).toBeUndefined();
    expect(parseCreditBudget("2.5")).toBeUndefined();
    expect(parseCreditBudget("abc")).toBeUndefined();
    expect(parseCreditBudget("1001")).toBeUndefined();
  });
});
