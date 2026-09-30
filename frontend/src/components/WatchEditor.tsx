import { useEffect, useId, useRef, useState } from "react";
import { CURRENCIES, currencySymbol } from "../lib/format";
import { CABIN_LABELS, formToRequest } from "../lib/search";
import type { SearchForm } from "../lib/search";
import { describeWatch, formToWatchUpdate, watchToForm } from "../lib/watches";
import type { CabinClass, Watch, WatchUpdate } from "../types";
import Icon from "./Icon";
import PassengersPicker from "./PassengersPicker";
import Segmented from "./Segmented";
import "./watches.css";

const todayIso = () => new Date().toISOString().slice(0, 10);

/** Inline editor for a watch's criteria and threshold — the same controls as
 *  the search form, minus the route (a watch's identity; delete to change it). */
export default function WatchEditor({
  watch,
  onSave,
  onCancel,
}: {
  watch: Watch;
  onSave: (patch: WatchUpdate) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<SearchForm>(() => watchToForm(watch));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const priceRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLFormElement>(null);
  const uid = useId();
  const set = (patch: Partial<SearchForm>) => setForm((f) => ({ ...f, ...patch }));
  const roundTrip = form.trip_type === "round_trip";
  const words = describeWatch(formToRequest(form));

  useEffect(() => {
    rootRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);

  async function submit() {
    const patch = formToWatchUpdate(form);
    if (!patch) {
      setError("Set the price you want to be alerted at.");
      priceRef.current?.focus();
      return;
    }
    setError(null);
    setSaving(true);
    const ok = await onSave(patch);
    setSaving(false);
    if (!ok) setError("Couldn't save — check the values and try again.");
  }

  return (
    <form
      ref={rootRef}
      className="watch-editor"
      aria-label={`Edit watch ${watch.origin} to ${watch.destination}`}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !(e.target as HTMLElement).closest(".popover")) onCancel();
      }}
    >
      <div className="sf-top">
        <Segmented
          label="Trip type"
          size="sm"
          value={form.trip_type}
          onChange={(trip_type) => set({ trip_type })}
          options={[
            { value: "round_trip", label: "Round trip" },
            { value: "one_way", label: "One way" },
          ]}
        />
        <PassengersPicker value={form.passengers} onChange={(passengers) => set({ passengers })} />
        <label className="pill-select pill-native">
          <span className="sr-only">Cabin class</span>
          <select value={form.cabin_class} onChange={(e) => set({ cabin_class: e.target.value as CabinClass })}>
            {(Object.keys(CABIN_LABELS) as CabinClass[]).map((c) => (
              <option key={c} value={c}>
                {CABIN_LABELS[c]}
              </option>
            ))}
          </select>
          <Icon name="chevronDown" size={14} />
        </label>
      </div>

      <div className="sf-dates">
        <div className="sf-dates-head">
          <span className="field-label">When</span>
          <Segmented
            label="Date mode"
            size="sm"
            value={form.date_mode}
            onChange={(date_mode) => set({ date_mode })}
            options={[
              { value: "flexible", label: "Flexible" },
              { value: "exact", label: "Exact dates" },
            ]}
          />
        </div>
        {form.date_mode === "flexible" ? (
          <input
            className="input"
            value={form.dates_hint}
            onChange={(e) => set({ dates_hint: e.target.value })}
            placeholder="Describe it, e.g. “a long weekend in November”"
            aria-label="Flexible dates"
          />
        ) : (
          <div className="sf-exact we-exact">
            <label className="field">
              <span className="field-label">Depart from</span>
              <input
                type="date"
                min={todayIso()}
                value={form.depart_from}
                onChange={(e) =>
                  set({
                    depart_from: e.target.value,
                    depart_to: form.depart_to && form.depart_to < e.target.value ? e.target.value : form.depart_to,
                  })
                }
                required
              />
            </label>
            <label className="field">
              <span className="field-label">Depart by</span>
              <input type="date" min={form.depart_from || todayIso()} value={form.depart_to} onChange={(e) => set({ depart_to: e.target.value })} />
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
                    onChange={(e) => set({ nights_min: e.target.value })}
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
                    onChange={(e) => set({ nights_max: e.target.value })}
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="sf-options we-options">
        <div className="sf-option">
          <span className="field-label">Stops</span>
          <Segmented
            label="Stops"
            size="sm"
            value={form.max_stops}
            onChange={(max_stops) => set({ max_stops })}
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
            <button type="button" className={`chip chip-toggle${form.cabin_bag ? " chip-active" : ""}`} aria-pressed={form.cabin_bag} onClick={() => set({ cabin_bag: !form.cabin_bag })}>
              <Icon name="bag" size={14} /> Cabin trolley
            </button>
            <button type="button" className={`chip chip-toggle${form.checked_bag ? " chip-active" : ""}`} aria-pressed={form.checked_bag} onClick={() => set({ checked_bag: !form.checked_bag })}>
              <Icon name="luggage" size={14} /> 20kg checked
            </button>
          </div>
        </div>
        <div className="sf-option">
          <span className="field-label" id={`${uid}-price`}>
            Alert at or under
          </span>
          <div className="price-input">
            <select aria-label="Currency" value={form.currency} onChange={(e) => set({ currency: e.target.value })}>
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
              placeholder="Max total"
              aria-labelledby={`${uid}-price`}
              aria-invalid={!!error && !formToWatchUpdate(form)}
              value={form.max_price}
              onChange={(e) => set({ max_price: e.target.value })}
            />
          </div>
        </div>
        <label className="switch">
          <input type="checkbox" checked={form.nearby_airports} onChange={(e) => set({ nearby_airports: e.target.checked })} />
          <span className="switch-track" aria-hidden="true" />
          <span>Include nearby airports</span>
        </label>
      </div>

      <p className="watch-words small" aria-live="polite">
        {words.search} <strong>{words.alert}</strong>
      </p>
      {error && (
        <p className="we-error small" role="alert">
          <Icon name="alert" size={14} /> {error}
        </p>
      )}
      <div className="we-actions">
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>
          <Icon name="check" size={14} /> {saving ? "Saving…" : "Save changes"}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
