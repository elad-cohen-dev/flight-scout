import { useEffect, useState } from "react";
import { fetchUsage } from "../api";
import { usageMeter } from "../lib/providers";
import type { UsageReport } from "../types";
import "./providers.css";

/** "Live searches this month: 12 / 230" — hidden when live search isn't configured.
 *  Re-fetches whenever `refreshKey` changes (e.g. after each search). */
export default function UsageMeter({ refreshKey }: { refreshKey?: string }) {
  const [report, setReport] = useState<UsageReport | null>(null);
  useEffect(() => {
    let alive = true;
    fetchUsage()
      .then((r) => alive && setReport(r))
      .catch(() => {}); // best-effort footer
    return () => {
      alive = false;
    };
  }, [refreshKey]);
  const meter = usageMeter(report);
  if (!meter) return null;
  const pct = meter.budget ? Math.min(100, (meter.used / meter.budget) * 100) : 0;
  return (
    <div className={`usage-meter${meter.low ? " usage-low" : ""}`} title="Live Google Flights searches (SerpApi) used this calendar month. Cached fares and repeats within an hour are free.">
      <span>{meter.text}</span>
      <span className="usage-track" role="meter" aria-valuemin={0} aria-valuemax={meter.budget} aria-valuenow={meter.used} aria-label="Live searches used">
        <span className="usage-fill" style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}
