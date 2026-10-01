import { sourceBadge } from "../lib/providers";
import type { StoredDeal } from "../types";
import "./providers.css";

/** "Google Flights · live" / "Aviasales · cached 2d" / "Web search"; nothing for legacy rows. */
export default function SourceBadge({ deal }: { deal: StoredDeal }) {
  const info = sourceBadge(deal);
  if (!info) return null;
  return (
    <span className={`badge badge-source badge-source-${info.tone}`} title={info.tip} tabIndex={0} aria-label={`${info.label}: ${info.tip}`}>
      <span className="source-dot" aria-hidden="true" />
      {info.label}
    </span>
  );
}
