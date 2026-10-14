import { useMemo } from "react";
import { CURRENCIES, currencySymbol } from "../lib/format";
import { CABIN_LABELS, DEFAULT_DATES_HINT } from "../lib/search";
import type { SearchForm as Form } from "../lib/search";
import type { CabinClass } from "../types";
import AirportInput from "./AirportInput";
import HolidayPicks from "./HolidayPicks";
import Icon from "./Icon";
import PassengersPicker from "./PassengersPicker";
import Segmented from "./Segmented";

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

export default function SearchForm({
  form,
  onChange,
  onSearch,
  onWatch,
  busy,
  priceRef,
}: {
  form: Form;
  onChange: (patch: Partial<Form>) => void;
  onSearch: () => void;
  onWatch: () => void;
  busy: boolean;
  priceRef: React.RefObject<HTMLInputElement>;
}) {
  const suggestions = useMemo(dateSuggestions, []);
  const roundTrip = form.trip_type === "round_trip";

  return (
    <form
      className="search-card card"
      onSubmit={(e) => {
        e.preventDefault();
        onSearch();
      }}
    >
      <div className="sf-top">
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
        <PassengersPicker value={form.passengers} onChange={(passengers) => onChange({ passengers })} />
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

      <div className="sf-route">
        <AirportInput
          label="From"
          value={form.origin}
          onChange={(origin) => onChange({ origin })}
          placeholder="City or airport, e.g. TLV"
          required
        />
        <button
          type="button"
          className="icon-btn round swap-btn"
          aria-label="Swap origin and destination"
          title="Swap"
          onClick={() => onChange({ origin: form.destination, destination: form.origin })}
        >
          <Icon name="swap" size={16} />
        </button>
        <AirportInput
          label="To"
          value={form.destination}
          onChange={(destination) => onChange({ destination })}
          placeholder="City, country, region or “anywhere”"
          required
        />
      </div>

      <div className="sf-dates">
        <div className="sf-dates-head">
          <span className="field-label">When</span>
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
        </div>
        <HolidayPicks form={form} onChange={onChange} />
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
              <input
                type="date"
                min={form.depart_from || todayIso()}
                value={form.depart_to}
                onChange={(e) => onChange({ depart_to: e.target.value })}
              />
            </label>
            {roundTrip && (
              <div className="field nights">
                <span className="field-label">Trip length (nights)</span>
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
      </div>

      <div className="sf-options">
        <div className="sf-option">
          <span className="field-label">Stops</span>
          <Segmented
            label="Stops"
            size="sm"
            value={form.max_stops}
            onChange={(max_stops) => onChange({ max_stops })}
            options={[
              { value: null, label: "Any" },
              { value: 0, label: "Direct" },
              { value: 1, label: "≤ 1 stop" },
            ]}
          />
        </div>
        <div className="sf-option">
          <span className="field-label">Bags included in price</span>
          <div className="chip-row">
            <button
              type="button"
              className={`chip chip-toggle${form.cabin_bag ? " chip-active" : ""}`}
              aria-pressed={form.cabin_bag}
              title="Prices include a 10kg cabin trolley (fees added where the fare excludes it)"
              onClick={() => onChange({ cabin_bag: !form.cabin_bag })}
            >
              <Icon name="bag" size={14} /> Cabin trolley
            </button>
            <button
              type="button"
              className={`chip chip-toggle${form.checked_bag ? " chip-active" : ""}`}
              aria-pressed={form.checked_bag}
              title="Prices include a 20kg+ checked bag (fees added where the fare excludes it)"
              onClick={() => onChange({ checked_bag: !form.checked_bag })}
            >
              <Icon name="luggage" size={14} /> 20kg checked
            </button>
          </div>
        </div>
        <div className="sf-option">
          <span className="field-label" id="max-price-label">
            Max total price
          </span>
          <div className="price-input">
            <select
              aria-label="Currency"
              value={form.currency}
              onChange={(e) => onChange({ currency: e.target.value })}
            >
              {CURRENCIES.map((c) => (
                <option key={c} value={c}>
                  {currencySymbol(c)} {c}
                </option>
              ))}
            </select>
            <input
              ref={priceRef}
              type="number"
              min={1}
              inputMode="numeric"
              placeholder="No limit"
              aria-labelledby="max-price-label"
              value={form.max_price}
              onChange={(e) => onChange({ max_price: e.target.value })}
            />
          </div>
        </div>
        <label className="switch">
          <input
            type="checkbox"
            checked={form.nearby_airports}
            onChange={(e) => onChange({ nearby_airports: e.target.checked })}
          />
          <span className="switch-track" aria-hidden="true" />
          <span>Include nearby airports</span>
        </label>
      </div>

      <div className="sf-actions">
        <button type="submit" className="btn btn-primary btn-lg" disabled={busy}>
          <Icon name="search" size={18} /> {busy ? "Searching…" : "Search flights"}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-lg"
          onClick={onWatch}
          disabled={busy}
          title="Re-check this route daily and alert when a deal is at or under your max price"
        >
          <Icon name="bell" size={18} /> Watch this route
        </button>
        <span className="muted small sf-hint">A search takes 1–3 minutes — the agent browses live fare sites.</span>
      </div>
    </form>
  );
}
