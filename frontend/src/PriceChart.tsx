import { useEffect, useMemo, useRef, useState } from "react";
import type { HistoryPoint } from "./types";

const WIDE = { W: 640, H: 220, PAD: { top: 16, right: 16, bottom: 28, left: 48 } };
// The SVG scales to the container width, so a 640-unit viewBox on a 350px-wide
// phone would render the 11px labels at ~6px. A narrower viewBox keeps them legible.
const NARROW = { W: 360, H: 210, PAD: { top: 14, right: 12, bottom: 26, left: 44 } };
const NARROW_QUERY = "(max-width: 520px)";

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.matchMedia(NARROW_QUERY).matches
  );
  useEffect(() => {
    const mql = window.matchMedia(NARROW_QUERY);
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    mql.addEventListener("change", onChange);
    setNarrow(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) {
    const pad = Math.max(1, Math.abs(min) * 0.1);
    min -= pad;
    max += pad;
  }
  const span = max - min;
  const step = Math.pow(10, Math.floor(Math.log10(span / count)));
  const err = span / count / step;
  const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
  const tick = mult * step;
  const start = Math.ceil(min / tick) * tick;
  const ticks: number[] = [];
  for (let v = start; v <= max + 1e-9; v += tick) ticks.push(Number(v.toFixed(6)));
  return ticks;
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function PriceChart({
  points,
  currency,
  formatPrice,
}: {
  points: HistoryPoint[];
  currency: string;
  formatPrice: (price: number, currency: string) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const { W, H, PAD } = useNarrow() ? NARROW : WIDE;

  const { xs, ys, yTicks } = useMemo(() => {
    const times = points.map((p) => new Date(p.found_at).getTime());
    const prices = points.map((p) => p.price);
    const tMin = Math.min(...times);
    const tMax = Math.max(...times);
    const yTicks = niceTicks(Math.min(...prices), Math.max(...prices));
    const yMin = Math.min(yTicks[0], Math.min(...prices));
    const yMax = Math.max(yTicks[yTicks.length - 1], Math.max(...prices));
    const plotW = W - PAD.left - PAD.right;
    const plotH = H - PAD.top - PAD.bottom;
    const xs = times.map((t) =>
      tMax === tMin ? PAD.left + plotW / 2 : PAD.left + ((t - tMin) / (tMax - tMin)) * plotW
    );
    const ys = prices.map((p) => PAD.top + (1 - (p - yMin) / (yMax - yMin)) * plotH);
    const yFor = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * plotH;
    return { xs, ys, yTicks: yTicks.map((v) => ({ v, y: yFor(v) })) };
  }, [points, W, H, PAD]);

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = svgRef.current!.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * W;
    let best = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - mx) < Math.abs(xs[best] - mx)) best = i;
    setHover(best);
  }

  const line = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  const h = hover != null ? points[hover] : null;

  return (
    <div className="chart-wrap">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="price-chart"
        onPointerDown={onMove}
        onPointerMove={onMove}
        // A touch pointer "leaves" the moment the finger lifts; clearing then would
        // make the tooltip unreadable on a phone, so it persists until the next tap.
        onPointerLeave={(e) => e.pointerType === "mouse" && setHover(null)}
        role="img"
        aria-label={`Best price over time, ${points.length} search runs`}
      >
        {yTicks.map(({ v, y }) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} className="chart-grid" />
            <text x={PAD.left - 8} y={y + 3.5} className="chart-label" textAnchor="end">
              {formatPrice(v, currency)}
            </text>
          </g>
        ))}
        <text x={PAD.left} y={H - 8} className="chart-label" textAnchor="start">
          {fmtDate(points[0].found_at)}
        </text>
        {points.length > 1 && (
          <text x={W - PAD.right} y={H - 8} className="chart-label" textAnchor="end">
            {fmtDate(points[points.length - 1].found_at)}
          </text>
        )}

        {hover != null && (
          <line x1={xs[hover]} x2={xs[hover]} y1={PAD.top} y2={H - PAD.bottom} className="chart-crosshair" />
        )}
        {points.length > 1 && <path d={line} className="chart-line" fill="none" />}
        {xs.map((x, i) => (
          <circle
            key={i}
            cx={x}
            cy={ys[i]}
            r={hover === i ? 5 : 4}
            className={`chart-dot${hover === i ? " hovered" : ""}`}
          />
        ))}
      </svg>

      {h != null && hover != null && (
        <div
          className="chart-tooltip"
          // Clamped so an edge point's tooltip stays inside the card instead of
          // hanging off the side of a narrow screen.
          style={{ left: `${Math.min(85, Math.max(15, (xs[hover] / W) * 100))}%` }}
        >
          <strong>{formatPrice(h.price, currency)}</strong>
          <span>
            {fmtDate(h.found_at)} · {h.deals} deal{h.deals !== 1 && "s"}
          </span>
        </div>
      )}

      <details className="chart-table">
        <summary>Data table</summary>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Best price</th>
              <th>Deals</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p, i) => (
              <tr key={i}>
                <td>{new Date(p.found_at).toLocaleString()}</td>
                <td>{formatPrice(p.price, currency)}</td>
                <td>{p.deals}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
