import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { quoteExplore, runExplore, HttpError } from "../api";
import {
  costLine,
  DURATION_LABELS,
  destinationKey,
  destinationWatchRequest,
  exploreRequest,
  filterDestinations,
  liveSearchRequest,
  loadExplore,
  monthOptions,
  REGION_LABELS,
  regionCounts,
  saveExplore,
  validMonth,
} from "../lib/explore";
import type { ExploreForm } from "../lib/explore";
import { CURRENCIES, currencySymbol, formatPrice, formatTripDate, plural, stopsLabel, timeAgo } from "../lib/format";
import { CABIN_LABELS, criteriaChips, passengersLabel, totalPassengers } from "../lib/search";
import type { CabinClass, ExploreDestination, ExploreQuote, ExploreResult, SearchRequest, TravelDuration, WatchCreate } from "../types";
import AirportInput from "./AirportInput";
import Icon from "./Icon";
import PassengersPicker from "./PassengersPicker";
import Segmented from "./Segmented";
import WatchPopover from "./WatchPopover";
import "./explore.css";

const ExploreMap = lazy(() => import("./ExploreMap"));

type Length = TravelDuration | "one_way";

// The last answer survives tab switches (it's free to show again).
let remembered: { key: string; result: ExploreResult } | null = null;

// Phones: once there are results, the criteria fold into a one-line summary.
const narrow = () => typeof window !== "undefined" && !!window.matchMedia?.("(max-width: 640px)").matches;

const requestKey = (f: ExploreForm) => JSON.stringify(exploreRequest(f));

/** "Next 6 months · 1 week · 2 travellers · direct · up to $300". */
function summaryLine(f: ExploreForm, months: { value: number; label: string }[]): string {
  const when = f.month ? (months.find((m) => m.value === f.month)?.label ?? "") : "Next 6 months";
  const parts = [when, f.trip_type === "one_way" ? "One way" : DURATION_LABELS[f.travel_duration], passengersLabel(f.passengers)];
  if (f.cabin_class !== "economy") parts.push(CABIN_LABELS[f.cabin_class]);
  if (f.direct_only) parts.push("direct");
  if (f.cabin_bag) parts.push("cabin bag");
  if (Number(f.max_price) > 0) parts.push(`up to ${formatPrice(Number(f.max_price), f.currency)}`);
  return parts.join(" · ");
}

/** FastAPI's {"detail": "..."} out of an HttpError ("409: {...}"). */
function errorDetail(e: HttpError): string {
  const body = e.message.replace(/^\d+:\s*/, "");
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    // not JSON
  }
  return body || "Explore failed.";
}

export default function ExploreTab({
  busy,
  onCheckLive,
  onCreateWatch,
  onError,
}: {
  busy: boolean; // a search is running: "Check live" waits
  onCheckLive: (req: SearchRequest) => void;
  onCreateWatch: (w: WatchCreate) => Promise<boolean>;
  onError: (e: unknown) => void;
}) {
  const [form, setForm] = useState<ExploreForm>(() => {
    const f = loadExplore();
    return { ...f, month: validMonth(f.month) };
  });
  const [quote, setQuote] = useState<ExploreQuote | null>(null);
  const [result, setResult] = useState<ExploreResult | null>(remembered?.result ?? null);
  const [resultKey, setResultKey] = useState<string | null>(remembered?.key ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(() => !!remembered && narrow());
  const listRef = useRef<HTMLOListElement>(null);
  const months = useMemo(() => monthOptions(), []);
  const key = requestKey(form);
  const patch = (p: Partial<ExploreForm>) => setForm((f) => ({ ...f, ...p }));

  useEffect(() => saveExplore(form), [form]);

  // The price of running: re-quoted (free, no Google call) as criteria change.
  // When the answer is already cached, show it right away — that's free too.
  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      const req = exploreRequest(form);
      quoteExplore(req)
        .then((q) => {
          if (!alive) return;
          setQuote(q);
          if (q.cached && resultKey !== key && !loading) {
            runExplore({ ...req, cached_only: true })
              .then((r) => alive && show(r, key))
              .catch(() => {}); // expired in between: the button still says what it costs
          }
        })
        .catch(() => alive && setQuote(null));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the request itself
  }, [key]);

  function show(r: ExploreResult, k: string) {
    remembered = { key: k, result: r };
    setResult(r);
    setResultKey(k);
    setSelected(null);
    setWatching(null);
    if (narrow()) setCollapsed(true);
    setQuote((q) => (q ? { ...q, cached: true, cost: 0, left: r.left } : q));
  }

  async function run() {
    if (loading) return;
    setLoading(true);
    setError(null);
    const k = key;
    try {
      show(await runExplore(exploreRequest(form)), k);
    } catch (e) {
      if (e instanceof HttpError) setError(errorDetail(e));
      else onError(e);
    } finally {
      setLoading(false);
    }
  }

  const all = result?.destinations ?? [];
  const shown = useMemo(() => filterDestinations(all, form), [all, form.max_price, form.region]); // eslint-disable-line react-hooks/exhaustive-deps
  const regions = useMemo(() => regionCounts(all), [all]);
  const stale = !!result && resultKey !== key;
  const cost = costLine(quote);
  const noBudget = !!quote?.configured && !quote.cached && quote.left <= 0;
  const length: Length = form.trip_type === "one_way" ? "one_way" : form.travel_duration;
  const pax = totalPassengers(form.passengers);

  /** The watch panel's input for a destination: its departure month, a trip length around the chosen one. */
  function watchSource(d: ExploreDestination) {
    const request = destinationWatchRequest(d, form);
    const nights = request.nights_min != null ? [`${request.nights_min}–${request.nights_max} nights`] : [];
    return {
      route: `${result?.origin ?? form.origin} → ${d.city}`,
      datesHint: request.dates_hint,
      chips: [...nights, ...criteriaChips(request)],
      request,
      latest: { minPrice: d.price, currency: d.currency },
    };
  }

  function select(k: string, fromMap = false) {
    setSelected((cur) => (cur === k && !fromMap ? null : k));
    setWatching(null);
    if (fromMap) {
      requestAnimationFrame(() =>
        listRef.current?.querySelector<HTMLElement>(`[data-key="${CSS.escape(k)}"] .xp-row`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }),
      );
    }
  }

  function onListKey(e: React.KeyboardEvent<HTMLOListElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = [...(listRef.current?.querySelectorAll<HTMLButtonElement>(".xp-row") ?? [])];
    const i = rows.indexOf(document.activeElement as HTMLButtonElement);
    if (i < 0) return;
    e.preventDefault();
    rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))]?.focus();
  }

  return (
    <div className="tab-explore">
      {collapsed ? (
        <div className="xp-summary card">
          <div className="xp-summary-text">
            <strong>Explore from {form.origin.trim() || "TLV"}</strong>
            <span className="muted small">{summaryLine(form, months)}</span>
          </div>
          <button type="button" className="btn btn-secondary btn-sm" aria-expanded={false} onClick={() => setCollapsed(false)}>
            <Icon name="edit" size={14} /> Change
          </button>
        </div>
      ) : (
        <form
          className="xp-criteria card"
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
        >
          <div className="xp-head">
            <div>
              <h2>Explore</h2>
              <p className="muted small">The cheapest places Google sees from your airport — on a map.</p>
            </div>
            {result && narrow() && (
              <button type="button" className="btn btn-ghost btn-sm" aria-expanded={true} onClick={() => setCollapsed(true)}>
                Hide
              </button>
            )}
          </div>

          <div className="xp-row-fields">
            <div className="xp-from">
              <AirportInput label="From" value={form.origin} onChange={(origin) => patch({ origin })} placeholder="City or airport, e.g. TLV" required />
            </div>
            <label className="field xp-month">
              <span className="field-label">When</span>
              <select value={form.month} onChange={(e) => patch({ month: Number(e.target.value) })}>
                <option value={0}>Next 6 months</option>
                {months.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="xp-options">
            <div className="sf-option">
              <span className="field-label">Trip length</span>
              <Segmented<Length>
                label="Trip length"
                size="sm"
                value={length}
                onChange={(v) => (v === "one_way" ? patch({ trip_type: "one_way" }) : patch({ trip_type: "round_trip", travel_duration: v }))}
                options={[
                  ...(Object.keys(DURATION_LABELS) as TravelDuration[]).map((d) => ({ value: d as Length, label: DURATION_LABELS[d] })),
                  { value: "one_way", label: "One way" },
                ]}
              />
            </div>
            <div className="sf-option">
              <span className="field-label">Travellers &amp; cabin</span>
              <div className="xp-inline">
                <PassengersPicker value={form.passengers} onChange={(passengers) => patch({ passengers })} />
                <label className="pill-select pill-native">
                  <span className="sr-only">Cabin class</span>
                  <select value={form.cabin_class} onChange={(e) => patch({ cabin_class: e.target.value as CabinClass })}>
                    {(Object.keys(CABIN_LABELS) as CabinClass[]).map((c) => (
                      <option key={c} value={c}>
                        {CABIN_LABELS[c]}
                      </option>
                    ))}
                  </select>
                  <Icon name="chevronDown" size={14} />
                </label>
              </div>
            </div>
            <div className="sf-option">
              <span className="field-label">Flights</span>
              <div className="chip-row">
                <button
                  type="button"
                  className={`chip chip-toggle${form.direct_only ? " chip-active" : ""}`}
                  aria-pressed={form.direct_only}
                  onClick={() => patch({ direct_only: !form.direct_only })}
                >
                  <Icon name="plane" size={14} /> Direct only
                </button>
                <button
                  type="button"
                  className={`chip chip-toggle${form.cabin_bag ? " chip-active" : ""}`}
                  aria-pressed={form.cabin_bag}
                  title="Prices include a carry-on per traveller (Google's bags filter)"
                  onClick={() => patch({ cabin_bag: !form.cabin_bag })}
                >
                  <Icon name="bag" size={14} /> Cabin bag
                </button>
              </div>
            </div>
            <div className="sf-option">
              <span className="field-label" id="xp-max-label">
                Max total price <span className="xp-free">free filter</span>
              </span>
              <div className="price-input">
                <select aria-label="Currency" value={form.currency} onChange={(e) => patch({ currency: e.target.value })}>
                  {CURRENCIES.map((c) => (
                    <option key={c} value={c}>
                      {currencySymbol(c)} {c}
                    </option>
                  ))}
                </select>
                <input
                  type="number"
                  min={1}
                  inputMode="numeric"
                  placeholder="No limit"
                  aria-labelledby="xp-max-label"
                  value={form.max_price}
                  onChange={(e) => patch({ max_price: e.target.value })}
                />
              </div>
            </div>
          </div>

          <div className="xp-actions">
            <button type="submit" className="btn btn-primary btn-lg" disabled={loading || noBudget || (!!quote && !quote.configured)}>
              <Icon name="search" size={18} /> {loading ? "Exploring…" : quote?.cached ? "Show destinations" : "Explore destinations"}
            </button>
            <span className={`xp-cost${quote?.cached ? " xp-cost-free" : ""}${noBudget ? " xp-cost-out" : ""}`} aria-live="polite">
              {quote && !quote.configured ? "Live Google search isn't configured on the server (SERPAPI_API_KEY)." : (cost ?? "Checking the cost…")}
            </span>
          </div>
        </form>
      )}

      {error && (
        <div className="xp-error card" role="alert">
          <Icon name="alert" size={18} /> {error}
        </div>
      )}

      {loading && !result && (
        <div className="xp-loading card" aria-live="polite">
          <span className="agent-dot" aria-hidden="true" /> Asking Google Explore for the cheapest destinations… usually 5–15 seconds.
        </div>
      )}

      {result && (
        <section className="xp-results" aria-labelledby="xp-results-title">
          <div className="section-head">
            <div>
              <h2 id="xp-results-title">
                {plural(shown.length, "destination")} from {result.origin}
              </h2>
              <p className="muted small">
                {result.month ? (months.find((m) => m.value === result.month)?.label ?? "") : "Next 6 months"} ·{" "}
                {result.trip_type === "one_way" ? "one way" : DURATION_LABELS[result.travel_duration].toLowerCase()} · prices for{" "}
                {passengersLabel(form.passengers)}, Google estimates ·{" "}
                {result.cached ? `from ${timeAgo(result.fetched_at)} (cached, free)` : "just fetched (1 live search)"}
              </p>
              {stale && (
                <p className="xp-stale small">
                  Criteria changed — these results are for the previous ones. {quote?.cached ? "" : "Explore again to update (1 live search)."}
                </p>
              )}
              {!!result.messages.length && (
                <ul className="search-notices">
                  {result.messages.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {regions.length > 1 && (
            <div className="chip-row xp-regions" role="group" aria-label="Region (free filter)">
              <button type="button" className={`chip${!form.region ? " chip-active" : ""}`} aria-pressed={!form.region} onClick={() => patch({ region: "" })}>
                All <span className="xp-count">{all.length}</span>
              </button>
              {regions.map((r) => (
                <button
                  key={r.region}
                  type="button"
                  className={`chip${form.region === r.region ? " chip-active" : ""}`}
                  aria-pressed={form.region === r.region}
                  onClick={() => patch({ region: form.region === r.region ? "" : r.region })}
                >
                  {REGION_LABELS[r.region]} <span className="xp-count">{r.count}</span>
                </button>
              ))}
            </div>
          )}

          <div className="xp-layout">
            <div className="xp-map-wrap card">
              <Suspense fallback={<div className="xm-map xm-loading muted small">Loading map…</div>}>
                <ExploreMap destinations={shown} selected={selected} onSelect={(k) => select(k, true)} />
              </Suspense>
            </div>

            {shown.length === 0 ? (
              <div className="empty card">
                <Icon name="filter" size={28} />
                <strong>Nothing under these filters</strong>
                <span className="muted">Raise the price cap or pick another region — it's free, same results.</span>
              </div>
            ) : (
              <ol className="xp-list" ref={listRef} onKeyDown={onListKey} aria-label="Destinations, cheapest first">
                {shown.map((d, i) => {
                  const k = destinationKey(d);
                  const open = selected === k;
                  return (
                    <li key={k} data-key={k} className={`xp-item card${open ? " xp-open" : ""}`}>
                      <button type="button" className="xp-row" aria-expanded={open} aria-controls={`xp-d-${i}`} onClick={() => select(k)}>
                        <span className="xp-rank" aria-hidden="true">
                          {i + 1}
                        </span>
                        {d.thumbnail ? (
                          <img className="xp-thumb" src={d.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" />
                        ) : (
                          <span className="xp-thumb xp-thumb-empty" aria-hidden="true">
                            <Icon name="plane" size={16} />
                          </span>
                        )}
                        <span className="xp-place">
                          <strong>{d.city}</strong>
                          <span className="muted small">
                            {d.country} · {formatTripDate(d.depart_date)}
                            {d.return_date ? ` – ${formatTripDate(d.return_date)}` : ""}
                          </span>
                          <span className="muted small">
                            {stopsLabel(d.stops)}
                            {d.duration ? ` · ${d.duration}` : ""}
                            {d.airline ? ` · ${d.airline}` : ""}
                          </span>
                        </span>
                        <span className="xp-price">
                          <strong>{formatPrice(d.price, d.currency)}</strong>
                          {pax > 1 && <span className="muted small">{formatPrice(d.price_per_person, d.currency)} pp</span>}
                        </span>
                      </button>
                      {open && (
                        <div className="xp-detail" id={`xp-d-${i}`}>
                          <dl className="xp-facts">
                            <div>
                              <dt>Dates</dt>
                              <dd>
                                {formatTripDate(d.depart_date)}
                                {d.return_date && (
                                  <>
                                    {" "}
                                    – {formatTripDate(d.return_date)}
                                    {d.nights ? ` · ${plural(d.nights, "night")}` : ""}
                                    {d.return_estimated && (
                                      <span className="badge badge-conf-estimated" title="Google gave no return date — estimated from the trip length">
                                        return estimated
                                      </span>
                                    )}
                                  </>
                                )}
                              </dd>
                            </div>
                            <div>
                              <dt>Flight</dt>
                              <dd>
                                {d.airline || "Airline not given"}
                                {d.airline_code ? ` (${d.airline_code})` : ""} · {stopsLabel(d.stops)}
                                {d.duration ? ` · ${d.duration}` : ""} · {result.origin} → {d.airport}
                              </dd>
                            </div>
                            <div>
                              <dt>Price</dt>
                              <dd>
                                {formatPrice(d.price, d.currency)} total for {passengersLabel(form.passengers)}
                                {pax > 1 ? ` (${formatPrice(d.price_per_person, d.currency)} per paying traveller)` : ""}{" "}
                                <span
                                  className="badge badge-conf-estimated"
                                  title="Google Travel Explore's cheapest price for these dates — not a confirmed itinerary"
                                >
                                  Google estimate
                                </span>
                              </dd>
                            </div>
                          </dl>
                          <div className="xp-detail-actions">
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              disabled={busy || (!!quote && quote.left <= 0)}
                              title="Runs a normal search for exactly these dates on Google Flights"
                              onClick={() => onCheckLive(liveSearchRequest(d, form))}
                            >
                              <Icon name="refresh" size={14} /> Check this fare live (1 search)
                            </button>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              aria-expanded={watching === k}
                              onClick={() => setWatching(watching === k ? null : k)}
                            >
                              <Icon name="bell" size={14} /> Watch this destination
                            </button>
                            {d.booking_links
                              .filter((l) => l.kind === "google_flights")
                              .slice(0, 1)
                              .map((l) => (
                                <a key={l.url} className="btn btn-ghost btn-sm" href={l.url} target="_blank" rel="noopener noreferrer">
                                  <Icon name="external" size={14} /> Google Flights
                                </a>
                              ))}
                          </div>
                          {watching === k && <WatchPopover group={watchSource(d)} onCreate={onCreateWatch} onClose={() => setWatching(null)} />}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
          {all.some((d) => d.lat == null) && <p className="muted small xp-foot">Destinations without coordinates are listed but not on the map.</p>}
        </section>
      )}

      {!result && !loading && !error && (
        <div className="empty card">
          <Icon name="sparkles" size={28} />
          <strong>Where can you fly cheaply?</strong>
          <span className="muted">
            Pick a month and trip length, then explore — one live search shows every destination, and re-filtering it is free for 6 hours.
          </span>
        </div>
      )}
    </div>
  );
}
