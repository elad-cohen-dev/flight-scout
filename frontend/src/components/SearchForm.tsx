import { useEffect, useMemo, useRef, useState } from "react";
import { CURRENCIES, currencySymbol, formatPrice } from "../lib/format";
import { CABIN_LABELS, DEFAULT_DATES_HINT, exactWindowLabel, maxCheckedBags } from "../lib/search";
import type { SearchForm as Form } from "../lib/search";
import type { CabinClass } from "../types";
import AirportInput from "./AirportInput";
import BagsPicker from "./BagsPicker";
import Dropdown from "./Dropdown";
import HolidayPicks from "./HolidayPicks";
import Icon from "./Icon";
import PassengersPicker from "./PassengersPicker";
import Segmented from "./Segmented";
import "./search.css";

function dateSuggestions(): string[] {
  const month = (offset: number) => {
    const d = new Date();
    d.setDate(1);
    d.setMonth(d.getMonth() + offset);
    return d.toLocaleDateString("en-US", { month: "long" });
  };
  return [DEFAULT_DATES_HINT, `${month(1)}`, `a long weekend in ${month(1)}`, `a week in ${month(2)}`, "next weekend"];
}

const todayIso = () => new Date().toISOString().slice(0, 10);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const num = (s: string) => (s.trim() === "" || Number.isNaN(Number(s)) ? null : Number(s));

let handledNudge = 0; // the last price nudge acted on (the form remounts on every tab switch)

/** What the "When" field reads: a holiday's name, the exact window, or the free text. */
function whenSummary(f: Form): string {
  if (f.date_mode === "exact") {
    if (f.holiday && f.dates_hint.trim()) return f.dates_hint.trim();
    const roundTrip = f.trip_type === "round_trip";
    return (
      exactWindowLabel({
        depart_from: f.depart_from || null,
        depart_to: f.depart_to || null,
        nights_min: roundTrip ? num(f.nights_min) : null,
        nights_max: roundTrip ? num(f.nights_max) : null,
      }) ?? "Pick dates"
    );
  }
  return cap(f.dates_hint.trim() || DEFAULT_DATES_HINT);
}

function bagsSummary(f: Form): string | undefined {
  const checked = Math.min(f.checked_bags, maxCheckedBags(f.passengers));
  const parts = [f.cabin_bag && "Trolley", checked > 0 && `${checked} checked`].filter(Boolean);
  return parts.length ? parts.join(" + ") : undefined;
}

/** The search bar, flight-search style: who and how on top, one line of
 *  where / when / Search, and the rest as filter chips that open on demand. */
export default function SearchForm({
  form,
  onChange,
  onSearch,
  onWatch,
  busy,
  priceNudge = 0,
}: {
  form: Form;
  onChange: (patch: Partial<Form>) => void;
  onSearch: () => void;
  onWatch: () => void;
  busy: boolean;
  priceNudge?: number; // bumped by the app to open the price filter (a watch needs a price)
}) {
  const suggestions = useMemo(dateSuggestions, []);
  const roundTrip = form.trip_type === "round_trip";
  const [priceOpen, setPriceOpen] = useState(false);
  const [forWatch, setForWatch] = useState(false); // the price panel was opened to set a watch's alert price
  const priceInput = useRef<HTMLInputElement>(null);
  const maxSet = Number(form.max_price) > 0;

  useEffect(() => {
    if (!priceNudge || priceNudge === handledNudge) return;
    handledNudge = priceNudge; // once per nudge: coming back to this tab later doesn't reopen the panel
    setForWatch(true);
    setPriceOpen(true);
  }, [priceNudge]);
  useEffect(() => {
    if (priceOpen) priceInput.current?.focus();
  }, [priceOpen]);
  const togglePrice = (open: boolean) => {
    setPriceOpen(open);
    if (!open) setForWatch(false);
  };

  function track() {
    if (maxSet) onWatch();
    else {
      setForWatch(true);
      setPriceOpen(true);
    }
  }

  const stops = form.max_stops === 0 ? "Direct" : form.max_stops != null ? `≤ ${form.max_stops} stop` : undefined;
  const bags = bagsSummary(form);
  const price = maxSet ? `Up to ${formatPrice(Number(form.max_price), form.currency)}` : form.currency !== "USD" ? `Price · ${form.currency}` : undefined;

  return (
    <form
      className="sb card"
      onSubmit={(e) => {
        e.preventDefault();
        onSearch();
      }}
    >
      <div className="sb-top">
        <Segmented
          label="Trip type"
          size="sm"
          value={form.trip_type}
          onChange={(trip_type) => onChange({ trip_type })}
          options={[
            { value: "round_trip", label: "Round trip" },
            { value: "one_way", label: "One way" },
          ]}
        />
        <PassengersPicker
          value={form.passengers}
          onChange={(passengers) => onChange({ passengers, checked_bags: Math.min(form.checked_bags, maxCheckedBags(passengers)) })}
        />
        <label className="pill-select pill-native">
          <span className="sr-only">Cabin class</span>
          <select value={form.cabin_class} onChange={(e) => onChange({ cabin_class: e.target.value as CabinClass })}>
            {(Object.keys(CABIN_LABELS) as CabinClass[]).map((c) => (
              <option key={c} value={c}>
                {CABIN_LABELS[c]}
              </option>
            ))}
          </select>
          <Icon name="chevronDown" size={14} />
        </label>
      </div>

      <div className="sb-bar">
        <div className="sb-route">
          <AirportInput label="From" value={form.origin} onChange={(origin) => onChange({ origin })} placeholder="City or airport" required />
          <button
            type="button"
            className="icon-btn round swap-btn"
            aria-label="Swap origin and destination"
            title="Swap"
            onClick={() => onChange({ origin: form.destination, destination: form.origin })}
          >
            <Icon name="swap" size={15} />
          </button>
          <AirportInput
            label="To"
            value={form.destination}
            onChange={(destination) => onChange({ destination })}
            placeholder="City, country, anywhere"
            required
          />
        </div>

        <Dropdown label="When" value={whenSummary(form)} icon="calendar" variant="field" align="right" panelLabel="Travel dates">
          {(close) => (
            <div className="sb-when">
              <Segmented
                label="Date mode"
                size="sm"
                value={form.date_mode}
                onChange={(date_mode) => onChange({ date_mode })}
                options={[
                  { value: "flexible", label: "Flexible" },
                  { value: "exact", label: "Exact dates" },
                ]}
              />
              {form.date_mode === "flexible" ? (
                <>
                  <input
                    className="input"
                    value={form.dates_hint}
                    onChange={(e) => onChange({ dates_hint: e.target.value })}
                    placeholder="Describe it, e.g. “a long weekend in November”"
                    aria-label="Flexible dates"
                  />
                  <div className="chip-row" aria-label="Date suggestions">
                    {suggestions.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={`chip${form.dates_hint === s ? " chip-active" : ""}`}
                        onClick={() => onChange({ dates_hint: s })}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <div className="sf-exact">
                  <label className="field">
                    <span className="field-label">Depart from</span>
                    <input
                      type="date"
                      min={todayIso()}
                      value={form.depart_from}
                      onChange={(e) =>
                        onChange({
                          depart_from: e.target.value,
                          depart_to: form.depart_to && form.depart_to < e.target.value ? e.target.value : form.depart_to,
                        })
                      }
                      required
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">Depart by</span>
                    <input type="date" min={form.depart_from || todayIso()} value={form.depart_to} onChange={(e) => onChange({ depart_to: e.target.value })} />
                  </label>
                  {roundTrip && (
                    <div className="field nights">
                      <span className="field-label">Nights</span>
                      <div className="nights-inputs">
                        <input
                          type="number"
                          min={0}
                          max={60}
                          inputMode="numeric"
                          placeholder="min"
                          aria-label="Minimum nights"
                          value={form.nights_min}
                          onChange={(e) => onChange({ nights_min: e.target.value })}
                        />
                        <span className="muted">–</span>
                        <input
                          type="number"
                          min={form.nights_min || 0}
                          max={60}
                          inputMode="numeric"
                          placeholder="max"
                          aria-label="Maximum nights"
                          value={form.nights_max}
                          onChange={(e) => onChange({ nights_max: e.target.value })}
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}
              <HolidayPicks form={form} onChange={onChange} />
              <div className="dd-foot">
                <button type="button" className="btn btn-primary btn-sm" onClick={close}>
                  Done
                </button>
              </div>
            </div>
          )}
        </Dropdown>

        <button type="submit" className="btn btn-primary sb-go" disabled={busy} title="A search takes 1–3 minutes — the agent checks live fares">
          <Icon name="search" size={18} /> {busy ? "Searching…" : "Search"}
        </button>
      </div>

      <div className="sb-filters">
        <Dropdown label="Stops" value={stops} active={!!stops}>
          {(close) => (
            <div className="sb-panel">
              <span className="field-label">Stops</span>
              <Segmented
                label="Stops"
                size="sm"
                value={form.max_stops}
                onChange={(max_stops) => {
                  onChange({ max_stops });
                  close();
                }}
                options={[
                  { value: null, label: "Any" },
                  { value: 0, label: "Direct" },
                  { value: 1, label: "≤ 1 stop" },
                ]}
              />
            </div>
          )}
        </Dropdown>

        <Dropdown label="Bags" value={bags} active={!!bags} icon="luggage">
          {(close) => (
            <div className="sb-panel sb-panel-wide">
              <BagsPicker cabinBag={form.cabin_bag} checkedBags={form.checked_bags} passengers={form.passengers} onChange={onChange} />
              <div className="dd-foot">
                <button type="button" className="btn btn-primary btn-sm" onClick={close}>
                  Done
                </button>
              </div>
            </div>
          )}
        </Dropdown>

        <Dropdown label="Price" value={price} active={maxSet} open={priceOpen} onOpenChange={togglePrice}>
          {(close) => (
            <div className="sb-panel sb-panel-wide">
              <span className="field-label" id="max-price-label">
                Max total price
              </span>
              <div className="price-input">
                <select aria-label="Currency" value={form.currency} onChange={(e) => onChange({ currency: e.target.value })}>
                  {CURRENCIES.map((c) => (
                    <option key={c} value={c}>
                      {currencySymbol(c)} {c}
                    </option>
                  ))}
                </select>
                <input
                  ref={priceInput}
                  type="number"
                  min={1}
                  inputMode="numeric"
                  placeholder="No limit"
                  aria-labelledby="max-price-label"
                  value={form.max_price}
                  onChange={(e) => onChange({ max_price: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return;
                    e.preventDefault(); // Enter closes the panel; it doesn't start a paid search
                    close();
                  }}
                />
              </div>
              <span className="muted small">
                {forWatch ? "Tracking needs a price: you get an alert when a fare is at or under it." : "For the whole party. Also the alert price when you track this route."}
              </span>
              <div className="dd-foot">
                {forWatch && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled={!maxSet || busy}
                    onClick={() => {
                      close();
                      onWatch();
                    }}
                  >
                    <Icon name="bell" size={14} /> Track at this price
                  </button>
                )}
                <button type="button" className="btn btn-primary btn-sm" onClick={close}>
                  Done
                </button>
              </div>
            </div>
          )}
        </Dropdown>

        <button
          type="button"
          className={`chip chip-toggle${form.nearby_airports ? " chip-active" : ""}`}
          aria-pressed={form.nearby_airports}
          onClick={() => onChange({ nearby_airports: !form.nearby_airports })}
        >
          Nearby airports
        </button>

        <button
          type="button"
          className="sb-track"
          onClick={track}
          disabled={busy}
          title="Re-check this route daily and alert when a fare is at or under your max price"
        >
          <Icon name="bell" size={15} /> Track prices
        </button>
      </div>
    </form>
  );
}
