import { describe, expect, it } from "vitest";
import { aiSpendLine } from "./aiSpend";
import type { AISpend, UsageReport } from "../types";

const ai = (over: Partial<AISpend> = {}): AISpend => ({
  configured: true, month: "2026-10", cost_usd: 1.8361, searches: 3, web_searches: 6, estimated: false,
  prices_as_of: "2026-10-01", budget_usd: null, over_budget: false, warning: "",
  research_model: "gpt-5.5", extract_model: "gpt-5.4-mini",
  models: [{ provider: "openai", model: "gpt-5.5", calls: 3, input_tokens: 240_000, cached_input_tokens: 0,
    output_tokens: 20_000, web_searches: 6, web_fetches: 0, cost_usd: 1.8, estimated: false }],
  ...over,
});
const report = (a: AISpend | null): UsageReport => ({ month: "2026-10", providers: [], ai: a });

describe("AI spend line", () => {
  it("shows the estimate and web searches with an explanatory tooltip", () => {
    const line = aiSpendLine(report(ai()))!;
    expect(line.text).toBe("AI this month: ~$1.84 · 6 web searches");
    expect(line.tone).toBe("ok");
    expect(line.tip).toContain("Estimate from the token counts");
    expect(line.tip).toContain("gpt-5.5: ~$1.80 (240k in / 20k out, 6 searches)");
  });

  it("flags unpriced models and budgets", () => {
    const line = aiSpendLine(report(ai({
      estimated: true, budget_usd: 2, warning: "AI spend this month is ~$1.84 of the $2.00 budget (92%).",
      models: [...ai().models, { ...ai().models[0], model: "mystery", cost_usd: null, estimated: true, web_searches: 1 }],
    })))!;
    expect(line.text).toBe("AI this month: ~$1.84+ · 6 web searches (budget $2.00)");
    expect(line.tone).toBe("warn");
    expect(line.tip).toContain("mystery: no price");
    expect(aiSpendLine(report(ai({ over_budget: true, warning: "used up" }))))?.toMatchObject({ tone: "over" });
  });

  it("hides when no LLM is configured and nothing was spent", () => {
    expect(aiSpendLine(report(ai({ configured: false, models: [] })))).toBeNull();
    expect(aiSpendLine(report(null))).toBeNull();
    expect(aiSpendLine(null)).toBeNull();
    expect(aiSpendLine(report(ai({ web_searches: 1 })))?.text).toBe("AI this month: ~$1.84 · 1 web search");
  });
});
