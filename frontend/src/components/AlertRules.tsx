import { useEffect, useState } from "react";
import type { Ref } from "react";
import { CURRENCIES, currencySymbol } from "../lib/format";
import { ALERT_LIMITS, clampRule, DEFAULT_ALERTS } from "../lib/watches";
import type { AlertSettings } from "../types";
import "./alerts.css";

type NumField = "new_low_days" | "under_typical_pct" | "big_drop_pct";

/** A rule's number, editable inline in its sentence. Commits while typing when
 *  in range; clamps to the backend's bounds on blur. */
function RuleNumber({
  field,
  value,
  onCommit,
  label,
  unit,
  disabled,
}: {
  field: NumField;
  value: number;
  onCommit: (field: NumField, n: number) => void;
  label: string;
  unit: string;
  disabled: boolean;
}) {
  const bounds = field === "new_low_days" ? ALERT_LIMITS.days : ALERT_LIMITS.pct;
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  return (
    <span className="ar-numwrap">
      <input
        className="ar-num"
        type="number"
        inputMode="numeric"
        min={bounds[0]}
        max={bounds[1]}
        aria-label={label}
        disabled={disabled}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          const n = Number(e.target.value);
          if (e.target.value.trim() && n >= bounds[0] && n <= bounds[1]) onCommit(field, Math.round(n));
        }}
        onBlur={() => {
          const n = clampRule(draft, bounds, DEFAULT_ALERTS[field]);
          setDraft(String(n));
          onCommit(field, n);
        }}
      />
      {unit}
    </span>
  );
}

function Rule({ on, onToggle, name, hint, children }: { on: boolean; onToggle: (on: boolean) => void; name: string; hint: string; children: React.ReactNode }) {
  return (
    <div className={`ar-rule${on ? "" : " ar-off"}`}>
      <label className="switch ar-switch">
        <input type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} aria-label={name} />
        <span className="switch-track" aria-hidden="true" />
      </label>
      <div className="ar-text">
        <div className="ar-line">{children}</div>
        <span className="muted small">{hint}</span>
      </div>
    </div>
  );
}

/** "Alert me when" — the absolute threshold plus the smart rules. */
export default function AlertRules({
  value,
  onChange,
  maxPrice,
  currency,
  onMaxPrice,
  onCurrency,
  priceRef,
  priceInvalid,
}: {
  value: AlertSettings;
  onChange: (patch: Partial<AlertSettings>) => void;
  maxPrice: string;
  currency: string;
  onMaxPrice: (v: string) => void;
  onCurrency: (v: string) => void;
  priceRef?: Ref<HTMLInputElement>;
  priceInvalid?: boolean;
}) {
  const commit = (field: NumField, n: number) => {
    if (value[field] !== n) onChange({ [field]: n });
  };
  const none = !value.threshold && !value.new_low && !value.under_typical && !value.big_drop;
  return (
    <fieldset className="alert-rules">
      <legend className="field-label">Alert me when</legend>
      <Rule
        on={value.threshold}
        onToggle={(threshold) => onChange({ threshold })}
        name="Price at or under target"
        hint="The total for everyone, converted from any currency."
      >
        <span>The price is at or under</span>
        <span className="price-input ar-price">
          <select aria-label="Currency" value={currency} onChange={(e) => onCurrency(e.target.value)}>
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
            aria-label="Alert price"
            aria-invalid={priceInvalid}
            value={maxPrice}
            onChange={(e) => onMaxPrice(e.target.value)}
          />
        </span>
      </Rule>
      <Rule on={value.new_low} onToggle={(new_low) => onChange({ new_low })} name="New low" hint="Starts after 5 daily checks.">
        <span>It’s the lowest price in</span>
        <RuleNumber field="new_low_days" value={value.new_low_days} onCommit={commit} label="Days for a new low" unit="days" disabled={!value.new_low} />
      </Rule>
      <Rule
        on={value.under_typical}
        onToggle={(under_typical) => onChange({ under_typical })}
        name="Under typical price"
        hint="Typical = median of the last 30 days’ best prices."
      >
        <RuleNumber
          field="under_typical_pct"
          value={value.under_typical_pct}
          onCommit={commit}
          label="Percent under typical"
          unit="%"
          disabled={!value.under_typical}
        />
        <span>under the typical price</span>
      </Rule>
      <Rule on={value.big_drop} onToggle={(big_drop) => onChange({ big_drop })} name="Big drop" hint="Compared with the previous check’s best price.">
        <span>It drops</span>
        <RuleNumber
          field="big_drop_pct"
          value={value.big_drop_pct}
          onCommit={commit}
          label="Percent drop since last check"
          unit="%"
          disabled={!value.big_drop}
        />
        <span>since the last check</span>
      </Rule>
      <p className={`ar-note small${none ? " ar-warn" : ""}`}>
        {none
          ? "Every rule is off — this watch will only show up in the daily digest."
          : "The same fare is re-alerted at most once a week, unless it drops another 5%."}
      </p>
    </fieldset>
  );
}
