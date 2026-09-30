import { useEffect, useMemo, useRef, useState } from "react";
import { addDays, addMonths, aroundDateRequest, buildCalendar, isoDay, monthEnd, monthGrid, monthRequest, STALE_DAYS, tierOf } from "../lib/calendar";
import type { DayCell, Tier } from "../lib/calendar";
import { currencySymbol, formatPrice, formatTripDate, plural, stopsLabel, timeAgo } from "../lib/format";
import type { SearchRequest, StoredDeal } from "../types";
import DealCard from "./DealCard";
import Icon from "./Icon";
import "./calendar.css";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TIER_LABEL: Record<Tier, string> = { low: "Low", mid: "Mid", high: "High" };
const MONTHS_AHEAD = 12; // how far "next month" goes, so any month can be pre-filled
const SHOWN_FARES = 3;

/** "$305" / "$1.2k" — fits a 44px day cell. */
function compactPrice(price: number, currency: string): string {
  const s = currencySymbol(currency);
  const sym = s.length === 1 ? s : "";
  const n = price >= 10_000 ? `${Math.round(price / 1000)}k` : price >= 1000 ? `${(price / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(Math.round(price));
  return `${sym}${n}`;
}

function monthTitle(ym: string): string {
  return new Date(`${ym}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function longDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

export default function PriceCalendar({
  deals,
  route,
  onSearchDates,
}: {
  deals: StoredDeal[];
  route: string;
  onSearchDates: (req: SearchRequest, note: string) => void;
}) {
  const [variant, setVariant] = useState<string | null>(null);
  const [directOnly, setDirectOnly] = useState(false);
  const cal = useMemo(() => buildCalendar(deals, route, { variant, directOnly }), [deals, route, variant, directOnly]);
  const today = isoDay(new Date());
  const thisMonth = today.slice(0, 7);
  const upcoming = cal.months.filter((m) => m >= thisMonth);
  const lastMonth = addMonths(thisMonth, MONTHS_AHEAD - 1);

  // Open on the first month with fares; with none, skip a nearly-over current month.
  const [month, setMonth] = useState(() => upcoming[0] ?? (monthEnd(thisMonth) < addDays(today, 7) ? addMonths(thisMonth, 1) : thisMonth));
  const [selected, setSelected] = useState<string | null>(null);
  const [focusDate, setFocusDate] = useState<string>(() => cal.cheapestDates[0] ?? today);
  const [showAll, setShowAll] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const moveFocus = useRef(false);
  const panelRef = useRef<HTMLHeadingElement>(null);

  // A filter change can empty the shown month; jump to the first one with data.
  useEffect(() => {
    if (upcoming.length && !upcoming.includes(month)) setMonth(upcoming[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cal]);

  useEffect(() => {
    if (!moveFocus.current) return;
    moveFocus.current = false;
    gridRef.current?.querySelector<HTMLButtonElement>(`[data-date="${focusDate}"]`)?.focus();
  }, [focusDate, month]);

  const weeks = monthGrid(month);
  const inMonth = (d: string) => d.slice(0, 7) === month;
  // The roving tab stop: the focused date when it's in view, else the month's first selectable day.
  const firstSelectable = weeks.flat().find((d): d is string => !!d && d >= today) ?? null;
  const tabStop = focusDate && inMonth(focusDate) && focusDate >= today ? focusDate : firstSelectable;

  const monthDays = [...cal.days.values()].filter((c) => inMonth(c.date));
  const day = selected ? cal.days.get(selected) ?? null : null;

  function go(delta: number) {
    const next = addMonths(month, delta);
    if (next < thisMonth || next > lastMonth) return;
    setMonth(next);
  }

  function onKey(e: React.KeyboardEvent, date: string) {
    const deltas: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    let next: string | null = null;
    const dow = (new Date(`${date}T00:00:00Z`).getUTCDay() + 6) % 7;
    if (e.key in deltas) next = addDays(date, deltas[e.key]);
    else if (e.key === "Home") next = addDays(date, -dow);
    else if (e.key === "End") next = addDays(date, 6 - dow);
    else if (e.key === "PageUp" || e.key === "PageDown") {
      const target = addMonths(date.slice(0, 7), e.key === "PageUp" ? -1 : 1);
      next = `${target}-${date.slice(8, 10)}`;
      while (next.slice(0, 7) !== target) next = addDays(next, -1); // Jan 31 -> Feb 28
    } else if (e.key === "Escape" && selected) {
      setSelected(null);
      return;
    } else return;
    e.preventDefault();
    if (next < today) next = today;
    if (next.slice(0, 7) > lastMonth) return;
    moveFocus.current = true;
    setFocusDate(next);
    if (!inMonth(next)) setMonth(next.slice(0, 7));
  }

  function pick(date: string) {
    setSelected((s) => (s === date ? null : date));
    setFocusDate(date);
    setShowAll(false);
  }

  useEffect(() => {
    if (selected) panelRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  const around = day && cal.latest ? aroundDateRequest(cal.latest, day.date) : null;

  function searchAround() {
    const req = around;
    if (req) onSearchDates(req, `Dates set to ${formatTripDate(req.depart_from!)} – ${formatTripDate(req.depart_to!)}. Review, then tap Search flights.`);
  }

  function fillMonth() {
    if (!cal.latest) return;
    const req = monthRequest(cal.latest, month);
    if (req) onSearchDates(req, `Dates set to ${monthTitle(month)}. Review, then tap Search flights.`);
  }

  const cheapest = new Set(cal.cheapestDates);
  const cur = cal.currency;
  const hasData = cal.days.size > 0;

  function cellLabel(date: string, c: DayCell | undefined): string {
    const base = longDate(date);
    if (date < today) return `${base}, past`;
    if (!c) return `${base}, no fares found`;
    const parts = [
      `${base}: best ${formatPrice(c.best.price, cur)}`,
      `${TIER_LABEL[tierOf(c.best.price, cal.min, cal.max)].toLowerCase()} price`,
      cheapest.has(date) ? "cheapest day" : "",
      c.stale ? `seen ${timeAgo(c.best.foundAt)}, may be outdated` : `seen ${timeAgo(c.best.foundAt)}`,
    ];
    return parts.filter(Boolean).join(", ");
  }

  return (
    <div className="pcal">
      <div className="pcal-toolbar">
        <div className="pcal-nav">
          <button type="button" className="icon-btn" onClick={() => go(-1)} disabled={month <= thisMonth} aria-label="Previous month">
            <Icon name="chevronRight" size={18} className="pcal-flip" />
          </button>
          <h4 className="pcal-month" aria-live="polite">
            {monthTitle(month)}
          </h4>
          <button type="button" className="icon-btn" onClick={() => go(1)} disabled={month >= lastMonth} aria-label="Next month">
            <Icon name="chevronRight" size={18} />
          </button>
        </div>
        <div className="pcal-filters">
          {cal.variants.length > 1 && (
            <label className="select-inline">
              <span className="sr-only">Fare kind</span>
              <select value={cal.variant?.key ?? ""} onChange={(e) => setVariant(e.target.value)}>
                {cal.variants.map((v) => (
                  <option key={v.key} value={v.key}>
                    {v.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="button" className={`chip${directOnly ? " chip-active" : ""}`} aria-pressed={directOnly} onClick={() => setDirectOnly((v) => !v)}>
            {directOnly && <Icon name="check" size={14} />} Direct only
          </button>
        </div>
      </div>

      {upcoming.length > 0 && (
        <div className="pcal-jump" role="group" aria-label="Months with fares">
          {upcoming.map((m) => {
            const n = [...cal.days.keys()].filter((d) => d.startsWith(m)).length;
            return (
              <button key={m} type="button" className={`chip${m === month ? " chip-active" : ""}`} aria-pressed={m === month} onClick={() => setMonth(m)}>
                {new Date(`${m}-01T00:00:00`).toLocaleDateString(undefined, { month: "short" })}
                <span className="pcal-jump-n">{plural(n, "day")}</span>
              </button>
            );
          })}
        </div>
      )}

      <div
        className="pcal-grid"
        role="grid"
        aria-label={`${cal.route.replace("->", "→")} fares by departure date, ${monthTitle(month)}`}
        ref={gridRef}
      >
        <div role="row" className="pcal-row pcal-head">
          {WEEKDAYS.map((w) => (
            <span role="columnheader" key={w} className="pcal-wd" aria-label={w}>
              <span aria-hidden="true">{w.slice(0, 1)}</span>
              <span className="pcal-wd-long" aria-hidden="true">
                {w.slice(1)}
              </span>
            </span>
          ))}
        </div>
        {weeks.map((week, wi) => (
          <div role="row" className="pcal-row" key={wi}>
            {week.map((date, di) => {
              if (!date) return <span role="gridcell" key={di} className="pcal-cell pcal-pad" />;
              const past = date < today;
              const c = past ? undefined : cal.days.get(date);
              const tier = c ? tierOf(c.best.price, cal.min, cal.max) : null;
              const cls = [
                "pcal-day",
                past && "is-past",
                date === today && "is-today",
                c ? `tier-${tier}` : !past && "is-empty",
                c?.stale && "is-stale",
                cheapest.has(date) && c && "is-cheapest",
                selected === date && "is-selected",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <span role="gridcell" key={date} className="pcal-cell" aria-selected={selected === date}>
                  <button
                    type="button"
                    data-date={date}
                    className={cls}
                    tabIndex={date === tabStop ? 0 : -1}
                    disabled={past}
                    aria-label={cellLabel(date, c)}
                    aria-expanded={c ? selected === date : undefined}
                    onClick={() => pick(date)}
                    onKeyDown={(e) => onKey(e, date)}
                    onFocus={() => setFocusDate(date)}
                  >
                    <span className="pcal-num" aria-hidden="true">
                      {+date.slice(8)}
                    </span>
                    {c && (
                      <span className="pcal-price" aria-hidden="true">
                        {compactPrice(c.best.price, cur)}
                      </span>
                    )}
                    {c && cheapest.has(date) && (
                      <span className="pcal-star" aria-hidden="true">
                        <Icon name="sparkles" size={11} />
                      </span>
                    )}
                  </button>
                </span>
              );
            })}
          </div>
        ))}
      </div>

      <div className="pcal-legend" aria-label="Legend">
        {hasData && (
          <>
            {(["low", "mid", "high"] as Tier[]).map((t) => (
              <span key={t} className="pcal-key">
                <span className={`pcal-swatch tier-${t}`} aria-hidden="true" />
                {TIER_LABEL[t]}
              </span>
            ))}
            <span className="pcal-key muted">
              {formatPrice(cal.min, cur)}–{formatPrice(cal.max, cur)}
            </span>
            <span className="pcal-key">
              <span className="pcal-swatch-star" aria-hidden="true">
                <Icon name="sparkles" size={12} />
              </span>
              Cheapest day
            </span>
            <span className="pcal-key">
              <span className="pcal-swatch is-stale" aria-hidden="true" />
              Seen {STALE_DAYS}+ days ago
            </span>
          </>
        )}
      </div>

      <p className="pcal-note muted small">
        {!hasData
          ? "No exact-date fares for this route yet. "
          : monthDays.length === 0
            ? `No fares found for ${monthTitle(month)}. `
            : `${plural(monthDays.length, "day")} with fares this month. `}
        {cal.undated > 0 &&
          `${plural(cal.undated, "deal")} with approximate dates${cal.undatedSample ? ` (like “${cal.undatedSample}”)` : ""} can't go on the calendar. `}
        {cal.otherCurrency > 0 && `${plural(cal.otherCurrency, "deal")} priced in another currency not shown. `}
        Prices are totals for the selected travellers.
      </p>

      {day && (
        <section className="pcal-detail card" aria-labelledby="pcal-detail-title">
          <div className="pcal-detail-head">
            <div>
              <h4 id="pcal-detail-title" ref={panelRef} tabIndex={-1}>
                {longDate(day.date)}
              </h4>
              <p className="muted small">
                Best {formatPrice(day.best.price, cur)}
                {day.nights != null && ` · ${plural(day.nights, "night")}, back ${formatTripDate(day.returnDate)}`}
                {` · ${stopsLabel(day.best.deal.stops)}`} · seen {timeAgo(day.best.foundAt)}
              </p>
            </div>
            <button type="button" className="icon-btn" onClick={() => setSelected(null)} aria-label="Close day details">
              <Icon name="x" size={18} />
            </button>
          </div>
          {day.stale && (
            <p className="pcal-callout">
              <Icon name="alert" size={15} /> This fare was last seen {timeAgo(day.best.foundAt)} — prices have likely moved. Search again to refresh it.
            </p>
          )}
          {day.staleLow && (
            <p className="pcal-callout pcal-callout-info">
              <Icon name="info" size={15} /> {formatPrice(day.staleLow.price, cur)} was seen {timeAgo(day.staleLow.foundAt)}, but newer searches found{" "}
              {formatPrice(day.best.price, cur)} at best.
            </p>
          )}
          <div className="pcal-fares">
            {(showAll ? day.fares : day.fares.slice(0, SHOWN_FARES)).map((s, i) => (
              <div key={s.deal.id} className={s.stale ? "pcal-fare-stale" : undefined}>
                <DealCard deal={s.deal} highlight={i === 0 && !s.stale} />
              </div>
            ))}
          </div>
          {day.fares.length > SHOWN_FARES && (
            <button type="button" className="btn btn-link" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show fewer" : `Show all ${day.fares.length} fares`}
            </button>
          )}
          <div className="pcal-actions">
            <button type="button" className="btn btn-primary" onClick={searchAround} disabled={!around}>
              <Icon name="search" size={16} /> Search around this date
            </button>
            {around && (
              <span className="muted small">
                Fills the Search tab with {formatTripDate(around.depart_from!)} – {formatTripDate(around.depart_to!)} — nothing runs until you tap Search.
              </span>
            )}
          </div>
        </section>
      )}

      <div className="pcal-actions pcal-month-actions">
        <button type="button" className="btn btn-secondary" onClick={fillMonth} disabled={!cal.latest}>
          <Icon name="calendar" size={16} /> Fill this month
        </button>
        <span className="muted small">Pre-fills a search for all of {monthTitle(month)} with this route's latest criteria — it doesn't start it.</span>
      </div>
    </div>
  );
}
