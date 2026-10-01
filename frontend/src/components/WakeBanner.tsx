import { useEffect, useState } from "react";
import { wakeProgress } from "../lib/jobs";

/** Shown while a sleeping free-tier server boots (~1 min). Progress is an
 *  estimate from elapsed time — the server reports nothing until it's up. */
export default function WakeBanner({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = Math.max(0, now - since);
  const seconds = Math.round(elapsed / 1000);
  const hint =
    elapsed > 3 * 60_000
      ? "Still no answer — the server may be down. This page keeps trying; reloading won't speed it up."
      : elapsed > 90_000
        ? "Taking longer than usual… still trying."
        : "Free hosting sleeps when idle; it usually takes about a minute. This page continues by itself.";
  return (
    <div className="wake-banner" role="status" aria-live="polite">
      <div className="wake-banner-text">
        <span className="agent-dot" aria-hidden="true" />
        <strong>Waking up the server…</strong>
        <span className="muted small wake-banner-time">{seconds}s</span>
      </div>
      <div className="wake-progress" aria-hidden="true">
        <div className="wake-progress-bar" style={{ width: `${Math.round(wakeProgress(elapsed) * 100)}%` }} />
      </div>
      <p className="muted small">{hint}</p>
    </div>
  );
}
