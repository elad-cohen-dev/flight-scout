// The "AI this month" line next to the live-search meter (backend app/llm_usage.py).
import type { AISpend, UsageReport } from "../types";

export interface AISpendInfo {
  text: string; // "AI this month: ~$1.84 · 6 web searches"
  tip: string;
  tone: "ok" | "warn" | "over";
}

const usd = (n: number) => `$${n < 10 ? n.toFixed(2) : n.toFixed(0)}`;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "es"}`;

function modelLine(m: AISpend["models"][number]): string {
  const cost = m.cost_usd == null ? "no price" : `~${usd(m.cost_usd)}`;
  const tokens = `${Math.round(m.input_tokens / 1000)}k in / ${Math.round(m.output_tokens / 1000)}k out`;
  const searches = m.web_searches ? `, ${plural(m.web_searches, "search")}` : "";
  return `${m.model}: ${cost} (${tokens}${searches})${m.estimated ? " — estimated price" : ""}`;
}

/** The AI spend line, or null when no LLM is configured and nothing was spent. */
export function aiSpendLine(report: UsageReport | null | undefined): AISpendInfo | null {
  const ai = report?.ai;
  if (!ai || (!ai.configured && ai.models.length === 0)) return null;
  const unpriced = ai.models.some((m) => m.cost_usd == null);
  const text = `AI this month: ~${usd(ai.cost_usd)}${unpriced ? "+" : ""} · ${plural(ai.web_searches, "web search")}`;
  const tip = [
    `Estimate from the token counts the model provider reported x list prices as of ${ai.prices_as_of} — not a bill.`,
    `${ai.searches} web-agent search${ai.searches === 1 ? "" : "es"} this month.`,
    ai.research_model && `Research: ${ai.research_model} · extraction: ${ai.extract_model}.`,
    ...ai.models.map(modelLine),
    ai.estimated && "Some prices are unverified or unknown; the total may be off.",
    ai.budget_usd != null && `Monthly AI budget: ${usd(ai.budget_usd)}.`,
    ai.warning,
  ].filter(Boolean).join("\n");
  const tone = ai.over_budget ? "over" : ai.warning ? "warn" : "ok";
  return { text: ai.budget_usd != null ? `${text} (budget ${usd(ai.budget_usd)})` : text, tip, tone };
}
