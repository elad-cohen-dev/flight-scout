import type { Advice, WatchPlan } from "../types";

/** Buy or wait (backend app/advice.py) and the watch credit plan (app/cadence.py), as the UI shows them. */

export type AdviceTone = "buy" | "good" | "wait" | "unknown";

const TONES: Record<Advice["verdict"], AdviceTone> = {
  buy_now: "buy",
  good_price: "good",
  wait: "wait",
  not_enough_data: "unknown",
};

export function adviceTone(a: Pick<Advice, "verdict">): AdviceTone {
  return TONES[a.verdict] ?? "unknown";
}

/** Show a chip? "Not enough data" only where the watch's own numbers are the context. */
export function showAdvice(a: Advice | null | undefined, includeUnknown = false): a is Advice {
  return !!a && (includeUnknown || a.verdict !== "not_enough_data");
}

/** "Next live check: in 2 days · 6/12 credits this month" (capitalised plan text), or null. */
export function planLine(plan: WatchPlan | null | undefined): string | null {
  if (!plan || plan.per_check <= 0 || !plan.text) return null;
  return plan.text.charAt(0).toUpperCase() + plan.text.slice(1);
}

/** Tooltip: why this cadence, and where the budget comes from. */
export function planTip(plan: WatchPlan): string {
  const budget = plan.budget_default
    ? `${plan.budget} live searches/month: a fair share of the monthly budget.`
    : `${plan.budget} live searches/month: set in the watch editor.`;
  const each = `${plan.per_check} live search${plan.per_check === 1 ? "" : "es"} per check`;
  return `Live check ${plan.reason} (${each}, ~${plan.per_month} this month). ${budget} The daily run still checks free cached fares in between.`;
}

/** The plan's tone: low once fewer than two checks are left this month. */
export function planLow(plan: WatchPlan): boolean {
  return plan.budget - plan.used < plan.per_check * 2;
}

/** Editor value -> PATCH value: "" = fair share (null), else a whole number ≥ 0; undefined = invalid. */
export function parseCreditBudget(text: string): number | null | undefined {
  const t = text.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isInteger(n) && n >= 0 && n <= 1000 ? n : undefined;
}
