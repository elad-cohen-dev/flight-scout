import { useMemo, useState } from "react";
import PriceChart from "../PriceChart";
import { formatPrice, plural, timeAgo } from "../lib/format";
import type { RouteHistory } from "../types";
import Icon from "./Icon";
import PriceTrend from "./PriceTrend";
import Sparkline from "./Sparkline";

export default function TrendsTab({ history, loading }: { history: RouteHistory[]; loading: boolean }) {
  const routes = useMemo(
    () =>
      history
        .filter((h) => h.points.length > 0)
        .sort((a, b) => b.points[b.points.length - 1].found_at.localeCompare(a.points[a.points.length - 1].found_at)),
    [history]
  );
  const [selected, setSelected] = useState<string | null>(null);

  if (loading) {
    return (
      <div className="tab-trends">
        <div className="skeleton skeleton-chart" />
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" />
      </div>
    );
  }
  if (routes.length === 0) {
    return (
      <div className="empty card">
        <Icon name="chart" size={32} />
        <strong>No price trends yet</strong>
        <span className="muted">Charts appear once a route has been searched — watches add a point every day.</span>
      </div>
    );
  }

  const active = routes.find((h) => h.route === selected) ?? routes[0];
  const pts = active.points;
  const prices = pts.map((p) => p.price);
  const low = Math.min(...prices);
  const latest = pts[pts.length - 1];
  const first = pts[0];
  const change = latest.price - first.price;

  return (
    <div className="tab-trends">
      <div className="page-head">
        <div>
          <h2>Price trends</h2>
          <p className="muted small">Best price per search run, per route.</p>
        </div>
      </div>

      <section className="card trend-card" aria-labelledby="trend-title">
        <div className="trend-card-head">
          <h3 id="trend-title">{active.route.replace("->", "→")}</h3>
          <span className="muted small">
            {plural(pts.length, "run")} · last {timeAgo(latest.found_at)}
          </span>
        </div>
        <div className="stat-row">
          <div className="stat">
            <span className="muted small">Latest best</span>
            <strong>{formatPrice(latest.price, active.currency)}</strong>
          </div>
          <div className="stat">
            <span className="muted small">Lowest seen</span>
            <strong className="text-good">{formatPrice(low, active.currency)}</strong>
          </div>
          <div className="stat">
            <span className="muted small">Since first run</span>
            <strong className={change < 0 ? "text-good" : change > 0 ? "text-bad" : ""}>
              {change === 0 ? "No change" : `${change < 0 ? "−" : "+"}${formatPrice(Math.abs(change), active.currency)}`}
            </strong>
          </div>
        </div>
        <PriceChart points={pts} currency={active.currency} formatPrice={formatPrice} />
        {pts.length < 2 && (
          <p className="muted small">One data point so far — the chart fills in as runs accumulate.</p>
        )}
      </section>

      <h3 className="section-title">
        All routes <span className="section-count">{routes.length}</span>
      </h3>
      <div className="route-list card" role="list">
        {routes.map((h) => {
          const last = h.points[h.points.length - 1];
          const prev = h.points.length > 1 ? h.points[h.points.length - 2].price : null;
          return (
            <button
              key={h.route}
              role="listitem"
              className={`route-row${h.route === active.route ? " active" : ""}`}
              onClick={() => {
                setSelected(h.route);
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
              aria-current={h.route === active.route}
            >
              <span className="route-name">
                {h.route.replace("->", "→")}
                <span className="muted small">{plural(h.points.length, "run")}</span>
              </span>
              <span className="route-spark">
                <Sparkline values={h.points.map((p) => p.price)} />
              </span>
              <span className="route-price">
                <strong>{formatPrice(last.price, h.currency)}</strong>
                <PriceTrend current={last.price} previous={prev} currency={h.currency} />
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
