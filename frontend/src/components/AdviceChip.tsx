import { adviceTone } from "../lib/advice";
import type { Advice } from "../types";
import "./advice.css";

/** "Buy now" / "Good price" / "Wait" / "Not enough data" (backend app/advice.py),
 *  with the one-line reason beside it or as the tooltip. */
export default function AdviceChip({ advice, withReason = false }: { advice: Advice; withReason?: boolean }) {
  const chip = (
    <span className={`advice-chip advice-${adviceTone(advice)}`} title={withReason ? undefined : advice.reason} tabIndex={withReason ? undefined : 0}>
      {advice.label}
    </span>
  );
  if (!withReason) return chip;
  return (
    <div className="advice" aria-label={`${advice.label}: ${advice.reason}`}>
      {chip}
      <span className="advice-reason">{advice.reason}</span>
    </div>
  );
}
