import { useMemo, useState } from "react";
import PriceChart from "../PriceChart";
import { routeVariants, runPoints } from "../lib/calendar";
import { formatPrice, plural, timeAgo } from "../lib/format";
import type { RouteHistory, SearchRequest, StoredDeal } from "../types";
import Icon from "./Icon";
import PriceCalendar from "./PriceCalendar";
import PriceCheckAccuracy from "./PriceCheckAccuracy";
import PriceTrend from "./PriceTrend";
import Segmented from "./Segmented";
import Sparkline from "./Sparkline";

type View = "chart" | "calendar";
const VIEW_KEY = "fs_trends_view";

function loadView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === "calendar" ? "calendar" : "chart";
  } catch {
    return "chart";
  }
}

export default function TrendsTab({
  history,
  loading,
  deals,
  onSearchDates,
}: {
  history: RouteHistory[];
  loading: boolean;
  deals: StoredDeal[];
  onSearchDates: (req: SearchRequest, note: string) => void;
}) {
  const routes = useMemo(
    () =>
      history
        .filter((h) => h.points.length > 0)
        .sort((a, b) => b.points[b.points.length - 1].found_at.localeCompare(a.points[a.points.length - 1].found_at)),
    [history]
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [variantChoice, setVariantChoice] = useState<string | null>(null);
  const [view, setView] = useState<View>(loadView);
  const keyOf = (h: RouteHistory) => h.route_key || h.route;
  // Each route's chart/sparkline follows one kind of fare (party · cabin ·
  // trip): prices are party totals, so runs for different parties don't mix.
  const series = useMemo(() => {
    const out = new Map<string, { variants: ReturnType<typeof routeVariants>; latest: ReturnType<typeof runPoints> }>();
    for (const h of routes) {
      const variants = routeVariants(deals, keyOf(h));
      out.set(h.route, { variants, latest: runPoints(deals, keyOf(h), variants[0]?.key ?? null) });
    }
    return out;
  }, [routes, deals]);

  function changeView(v: View) {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // remembering the view is best-effort
    }
  }

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
  const activeSeries = series.get(active.route);
  const variants = activeSeries?.variants ?? [];
  const variant = variants.find((v) => v.key === variantChoice) ?? variants[0] ?? null;
  const own = variant ? runPoints(deals, keyOf(active), variant.key) : null;
  // Fall back to the backend's per-route history when no deals are loaded.
  const pts = own && own.points.length ? own.points : active.points;
  const currency = own && own.points.length ? own.currency : active.currency;
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
          <p className="muted small">
            {view === "chart" ? "Best price per search run, per route." : "Best fare found for each departure date, per route."}
          </p>
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
            <strong>{formatPrice(latest.price, currency)}</strong>
          </div>
          <div className="stat">
            <span className="muted small">Lowest seen</span>
            <strong className="text-good">{formatPrice(low, currency)}</strong>
          </div>
          <div className="stat">
            <span className="muted small">Since first run</span>
            <strong className={change < 0 ? "text-good" : change > 0 ? "text-bad" : ""}>
              {change === 0 ? "No change" : `${change < 0 ? "−" : "+"}${formatPrice(Math.abs(change), currency)}`}
            </strong>
          </div>
        </div>
        <div className="trend-view-toggle">
          {variants.length > 1 && (
            <label className="select-inline">
              <span className="sr-only">Fare kind</span>
              <select
                value={variant?.key ?? ""}
                onChange={(e) => setVariantChoice(e.target.value)}
                title="Prices are totals for the whole party, so each kind of fare is charted separately"
              >
                {variants.map((v) => (
                  <option key={v.key} value={v.key}>
                    {v.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Segmented<View>
            label="Trend view"
            size="sm"
            options={[
              { value: "chart", label: "Chart" },
              { value: "calendar", label: "Calendar" },
            ]}
            value={view}
            onChange={changeView}
          />
        </div>
        {view === "chart" ? (
          <>
            <PriceChart points={pts} currency={currency} formatPrice={formatPrice} />
            {pts.length < 2 && (
              <p className="muted small">One data point so far — the chart fills in as runs accumulate.</p>
            )}
          </>
        ) : (
          <PriceCalendar key={active.route} deals={deals} route={keyOf(active)} variant={variant?.key ?? null} onSearchDates={onSearchDates} />
        )}
      </section>

      <PriceCheckAccuracy />
      <h3 className="section-title">
        All routes <span className="section-count">{routes.length}</span>
      </h3>
      <div className="route-list card" role="list">
        {routes.map((h) => {
          const s = series.get(h.route)?.latest;
          const hp = s && s.points.length ? s.points : h.points;
          const hc = s && s.points.length ? s.currency : h.currency;
          const last = hp[hp.length - 1];
          const prev = hp.length > 1 ? hp[hp.length - 2].price : null;
          return (
            <button
              key={h.route}
              role="listitem"
              className={`route-row${h.route === active.route ? " active" : ""}`}
              onClick={() => {
                setSelected(h.route);
                setVariantChoice(null);
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
              aria-current={h.route === active.route}
            >
              <span className="route-name">
                {h.route.replace("->", "→")}
                <span className="muted small">{plural(hp.length, "run")}</span>
              </span>
              <span className="route-spark">
                <Sparkline values={hp.map((p) => p.price)} />
              </span>
              <span className="route-price">
                <strong>{formatPrice(last.price, hc)}</strong>
                <PriceTrend current={last.price} previous={prev} currency={hc} />
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
