import { useEffect, useRef, useState } from "react";
import { CABIN_LABELS, formToRequest, maxCheckedBags } from "../lib/search";
import type { SearchForm } from "../lib/search";
import { parseCreditBudget } from "../lib/advice";
import { alertSettingsOf, describeAlertRules, describeWatch, formToWatchUpdate, watchToForm } from "../lib/watches";
import type { AlertSettings, CabinClass, Watch, WatchUpdate } from "../types";
import AlertRules from "./AlertRules";
import BagsPicker from "./BagsPicker";
import HolidayPicks from "./HolidayPicks";
import Icon from "./Icon";
import PassengersPicker from "./PassengersPicker";
import Segmented from "./Segmented";
import "./watches.css";
import "./advice.css";

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
  const [alerts, setAlerts] = useState<AlertSettings>(() => alertSettingsOf(watch));
  const [budget, setBudget] = useState(watch.credit_budget != null ? String(watch.credit_budget) : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const priceRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLFormElement>(null);
  const set = (patch: Partial<SearchForm>) => setForm((f) => ({ ...f, ...patch }));
  const roundTrip = form.trip_type === "round_trip";
  const words = describeWatch(formToRequest(form));
  const ruleWords = describeAlertRules(alerts);

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
    const credit_budget = parseCreditBudget(budget);
    if (credit_budget === undefined) {
      setError("Live searches a month: a whole number, or empty for a fair share.");
      return;
    }
    setError(null);
    setSaving(true);
    const ok = await onSave({ ...patch, alerts, credit_budget });
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
        <PassengersPicker
          value={form.passengers}
          onChange={(passengers) => set({ passengers, checked_bags: Math.min(form.checked_bags, maxCheckedBags(passengers)) })}
        />
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
        <HolidayPicks form={form} onChange={set} />
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
        <BagsPicker cabinBag={form.cabin_bag} checkedBags={form.checked_bags} passengers={form.passengers} onChange={set} />
        <label className="switch">
          <input type="checkbox" checked={form.nearby_airports} onChange={(e) => set({ nearby_airports: e.target.checked })} />
          <span className="switch-track" aria-hidden="true" />
          <span>Include nearby airports</span>
        </label>
      </div>

      <AlertRules
        value={alerts}
        onChange={(patch) => setAlerts((a) => ({ ...a, ...patch }))}
        maxPrice={form.max_price}
        currency={form.currency}
        onMaxPrice={(max_price) => set({ max_price })}
        onCurrency={(currency) => set({ currency })}
        priceRef={priceRef}
        priceInvalid={!!error && !formToWatchUpdate(form)}
      />

      <label className="field we-budget">
        <span className="field-label">Live searches a month</span>
        <input
          type="number"
          min={0}
          max={1000}
          inputMode="numeric"
          placeholder={watch.plan?.budget_default ? `fair share (${watch.plan.budget})` : "fair share"}
          aria-describedby={`we-budget-${watch.id}`}
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
        />
        <span className="muted small" id={`we-budget-${watch.id}`}>
          Caps this watch's live Google checks; the daily run checks less often to stay inside it. Empty = a fair share of the monthly budget.
        </span>
      </label>

      <p className="watch-words small" aria-live="polite">
        {words.search} <strong>{alerts.threshold ? words.alert : ""}</strong> {ruleWords}
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
