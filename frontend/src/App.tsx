import { useEffect, useMemo, useState } from "react";
import { createWatch, deleteWatch, fetchDeals, fetchHistory, fetchWatches, runSearch } from "./api";
import PriceChart from "./PriceChart";
import type { RouteHistory, SearchGroup, StoredDeal, Watch } from "./types";

const CURRENCY_SYMBOLS: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", ILS: "₪" };

function formatPrice(price: number, currency: string): string {
  const symbol = CURRENCY_SYMBOLS[currency];
  const amount = price.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return symbol ? `${symbol}${amount}` : `${amount} ${currency}`;
}

function timeAgo(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 90) return "just now";
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function groupDeals(deals: StoredDeal[]): SearchGroup[] {
  const groups = new Map<string, SearchGroup>();
  for (const deal of deals) {
    const key = `${deal.search_query}|${deal.found_at}`;
    let group = groups.get(key);
    if (!group) {
      group = { key, query: deal.search_query, foundAt: deal.found_at, deals: [], minPrice: Infinity };
      groups.set(key, group);
    }
    group.deals.push(deal);
    group.minPrice = Math.min(group.minPrice, deal.price);
  }
  const list = [...groups.values()];
  list.sort((a, b) => b.foundAt.localeCompare(a.foundAt));
  for (const g of list) g.deals.sort((a, b) => a.price - b.price);
  return list;
}

function DealRow({ deal }: { deal: StoredDeal }) {
  return (
    <div className="deal-row">
      <div className="deal-price">{formatPrice(deal.price, deal.currency)}</div>
      <div className="deal-main">
        <div className="deal-route">
          {deal.origin} → {deal.destination}
          <span className="deal-airline"> · {deal.airline}</span>
        </div>
        <div className="deal-meta">
          <span>
            {deal.depart_date}
            {deal.return_date && ` — ${deal.return_date}`}
          </span>
          {deal.duration && <span>{deal.duration}</span>}
          <span className={deal.stops === 0 ? "badge-direct" : ""}>
            {deal.stops === 0 ? "Direct" : `${deal.stops} stop${deal.stops > 1 ? "s" : ""}`}
          </span>
          {deal.times_seen > 1 && deal.first_found_at && (
            <span
              className="badge-seen"
              title="This exact deal keeps appearing in searches — likely a stable fare"
            >
              seen {deal.times_seen}× since {new Date(deal.first_found_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
            </span>
          )}
        </div>
        {deal.notes && <div className="deal-notes">{deal.notes}</div>}
      </div>
      <div className="deal-actions">
        {deal.booking_url && (
          <a href={deal.booking_url} target="_blank" rel="noreferrer" className="btn-book">
            Book
          </a>
        )}
        {deal.source_url && deal.source_url !== deal.booking_url && (
          <a href={deal.source_url} target="_blank" rel="noreferrer" className="btn-source">
            Source
          </a>
        )}
      </div>
    </div>
  );
}

function prettyQuery(query: string): string {
  return query.replace(" -> ", " → ").replace(/\s*\[watch #\d+\]/, " · watch");
}

function SearchGroupCard({ group, defaultOpen }: { group: SearchGroup; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const currency = group.deals[0]?.currency ?? "USD";
  return (
    <section className="group-card">
      <button className="group-header" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="group-chevron">{open ? "▾" : "▸"}</span>
        <span className="group-title">{prettyQuery(group.query)}</span>
        <span className="group-summary">
          {group.deals.length} deal{group.deals.length !== 1 && "s"} · from{" "}
          <strong>{formatPrice(group.minPrice, currency)}</strong> · {timeAgo(group.foundAt)}
        </span>
      </button>
      {open && <div className="group-body">{group.deals.map((d) => <DealRow key={d.id} deal={d} />)}</div>}
    </section>
  );
}

function WatchesPanel({
  watches,
  onDelete,
}: {
  watches: Watch[];
  onDelete: (id: number) => void;
}) {
  if (watches.length === 0) return null;
  return (
    <section className="watches-panel">
      <h2>🔔 Price watches</h2>
      {watches.map((w) => (
        <div className="watch-row" key={w.id}>
          <span className="watch-route">
            {w.origin} → {w.destination}
          </span>
          <span className="watch-meta">
            {w.dates_hint} · alert ≤ {formatPrice(w.max_price, w.currency)}
            {w.last_best_price != null && (
              <> · last best {formatPrice(w.last_best_price, w.currency)}</>
            )}
            {w.last_run_at && <> · checked {timeAgo(w.last_run_at)}</>}
          </span>
          <button className="btn-delete" title="Delete watch" onClick={() => onDelete(w.id)}>
            ✕
          </button>
        </div>
      ))}
      <p className="watch-hint">Watched routes are re-checked daily; you get a push when a deal drops below your threshold.</p>
    </section>
  );
}

function HistoryPanel({
  history,
  formatPrice,
}: {
  history: RouteHistory[];
  formatPrice: (price: number, currency: string) => string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const routes = history.filter((h) => h.points.length > 0);
  if (routes.length === 0) return null;

  const active = routes.find((h) => h.route === selected) ?? routes[0];
  return (
    <section className="history-panel">
      <h2>📈 Price history — {active.route.replace("->", "→")}</h2>
      {routes.length > 1 && (
        <div className="route-chips">
          {routes.map((h) => (
            <button
              key={h.route}
              className={`chip${h.route === active.route ? " chip-active" : ""}`}
              onClick={() => setSelected(h.route)}
            >
              {h.route.replace("->", "→")}
            </button>
          ))}
        </div>
      )}
      <PriceChart points={active.points} currency={active.currency} formatPrice={formatPrice} />
      {active.points.length < 2 && (
        <p className="watch-hint">One data point so far — the chart fills in as daily watch runs accumulate.</p>
      )}
    </section>
  );
}

export default function App() {
  const [deals, setDeals] = useState<StoredDeal[]>([]);
  const [watches, setWatches] = useState<Watch[]>([]);
  const [history, setHistory] = useState<RouteHistory[]>([]);
  const [origin, setOrigin] = useState("TLV");
  const [destination, setDestination] = useState("");
  const [datesHint, setDatesHint] = useState("anytime in the next 3 months");
  const [maxPrice, setMaxPrice] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [routeFilter, setRouteFilter] = useState("");
  const [directOnly, setDirectOnly] = useState(false);

  useEffect(() => {
    fetchDeals().then(setDeals).catch((e) => setError(String(e)));
    fetchWatches().then(setWatches).catch(() => {});
    fetchHistory().then(setHistory).catch(() => {});
  }, []);

  // deals change (new search / watch run) => history changes too
  useEffect(() => {
    if (deals.length > 0) fetchHistory().then(setHistory).catch(() => {});
  }, [deals.length]);

  const groups = useMemo(() => {
    let filtered = deals;
    if (routeFilter.trim()) {
      const q = routeFilter.trim().toLowerCase();
      filtered = filtered.filter(
        (d) =>
          d.origin.toLowerCase().includes(q) ||
          d.destination.toLowerCase().includes(q) ||
          d.airline.toLowerCase().includes(q)
      );
    }
    if (directOnly) filtered = filtered.filter((d) => d.stops === 0);
    return groupDeals(filtered);
  }, [deals, routeFilter, directOnly]);

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!origin || !destination) return;
    setSearching(true);
    setError(null);
    setNotice(null);
    try {
      const job = await runSearch({ origin, destination, dates_hint: datesHint });
      if (job.status === "error") {
        setError(job.error ?? "Search failed");
      } else {
        setDeals((prev) => [...(job.deals ?? []), ...prev]);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSearching(false);
    }
  }

  async function onAddWatch() {
    if (!origin || !destination || !maxPrice) {
      setError("To add a watch, fill origin, destination and a max price.");
      return;
    }
    setError(null);
    try {
      const watch = await createWatch({
        origin,
        destination,
        dates_hint: datesHint,
        max_price: Number(maxPrice),
      });
      setWatches((prev) => [...prev, watch]);
      setNotice(`Watching ${watch.origin} → ${watch.destination} for deals under ${formatPrice(watch.max_price, watch.currency)}.`);
    } catch (e) {
      setError(String(e));
    }
  }

  async function onDeleteWatch(id: number) {
    try {
      await deleteWatch(id);
      setWatches((prev) => prev.filter((w) => w.id !== id));
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="container">
      <header>
        <h1>✈️ Flight Scout</h1>
        <p className="subtitle">An agent that hunts the web for cheap flights</p>
      </header>

      <form className="search-form" onSubmit={onSearch}>
        <input value={origin} onChange={(e) => setOrigin(e.target.value)} placeholder="From (e.g. TLV)" required />
        <input value={destination} onChange={(e) => setDestination(e.target.value)} placeholder="To (e.g. London)" required />
        <input className="dates-input" value={datesHint} onChange={(e) => setDatesHint(e.target.value)} placeholder="When? (free text)" />
        <input
          className="price-input"
          type="number"
          min="1"
          value={maxPrice}
          onChange={(e) => setMaxPrice(e.target.value)}
          placeholder="Max $ (for watch)"
        />
        <button type="submit" disabled={searching}>
          {searching ? "Searching…" : "Find deals"}
        </button>
        <button type="button" className="btn-watch" onClick={onAddWatch} disabled={searching}>
          + Watch route
        </button>
      </form>

      {deals.length > 0 && (
        <div className="filter-bar">
          <input
            value={routeFilter}
            onChange={(e) => setRouteFilter(e.target.value)}
            placeholder="Filter results by city / airport / airline"
          />
          <label>
            <input type="checkbox" checked={directOnly} onChange={(e) => setDirectOnly(e.target.checked)} />
            Direct only
          </label>
        </div>
      )}

      {searching && (
        <div className="status">The agent is searching the live web — this usually takes a minute or two ☕</div>
      )}
      {error && <div className="status error">{error}</div>}
      {notice && <div className="status notice">{notice}</div>}

      <WatchesPanel watches={watches} onDelete={onDeleteWatch} />

      <HistoryPanel history={history} formatPrice={formatPrice} />

      <main>
        {groups.length === 0 && !searching ? (
          <div className="empty">
            {deals.length === 0 ? "No deals yet. Run a search above to get started." : "Nothing matches the filters."}
          </div>
        ) : (
          groups.map((group, i) => <SearchGroupCard key={group.key} group={group} defaultOpen={i === 0} />)
        )}
      </main>
    </div>
  );
}
