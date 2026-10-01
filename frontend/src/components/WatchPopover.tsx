import { useEffect, useId, useRef, useState } from "react";
import { CURRENCIES, currencySymbol, formatPrice } from "../lib/format";
import type { QueryGroup, Run } from "../lib/history";
import { suggestedThreshold, watchFromRequest } from "../lib/watches";
import type { WatchCreate } from "../types";
import Icon from "./Icon";
import "./watches.css";

/** What the panel needs: a History card's group, or an Explore destination shaped like one. */
export type WatchSource = Pick<QueryGroup, "route" | "datesHint" | "chips" | "request"> & { latest: Pick<Run, "minPrice" | "currency"> };

/** Inline "watch this search" panel on a History card (or an Explore
 *  destination): its criteria, plus an alert price ~10% under its latest best. */
export default function WatchPopover({
  group,
  onCreate,
  onClose,
}: {
  group: WatchSource;
  onCreate: (w: WatchCreate) => Promise<boolean>;
  onClose: () => void;
}) {
  const { latest } = group;
  const [price, setPrice] = useState(() => String(suggestedThreshold(latest.minPrice)));
  const [currency, setCurrency] = useState(latest.currency);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const uid = useId();

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  async function submit() {
    const value = Number(price);
    if (!group.request) return;
    if (!price.trim() || !(value > 0)) {
      setError("Enter the price you want to be alerted at.");
      inputRef.current?.focus();
      return;
    }
    setSaving(true);
    const ok = await onCreate(watchFromRequest(group.request, value, currency));
    setSaving(false);
    if (ok) onClose();
  }

  return (
    <form
      className="watch-pop"
      role="dialog"
      aria-labelledby={`${uid}-title`}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
    >
      <div className="watch-pop-head">
        <h4 id={`${uid}-title`}>
          <Icon name="bell" size={15} /> Watch {group.route}
        </h4>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
          <Icon name="x" size={16} />
        </button>
      </div>
      <p className="muted small watch-pop-hint">
        {[group.datesHint, ...group.chips].filter(Boolean).join(" · ")} — checked daily with these criteria; you get an alert when a
        fare is at or under your price.
      </p>
      <div className="watch-pop-row">
        <label className="sf-option">
          <span className="field-label" id={`${uid}-price`}>
            Alert at or under
          </span>
          <span className="price-input">
            <select aria-label="Currency" value={currency} onChange={(e) => setCurrency(e.target.value)}>
              {[...new Set([...CURRENCIES, latest.currency])].map((c) => (
                <option key={c} value={c}>
                  {currencySymbol(c)} {c}
                </option>
              ))}
            </select>
            <input
              ref={inputRef}
              type="number"
              min={1}
              inputMode="numeric"
              aria-labelledby={`${uid}-price`}
              aria-invalid={!!error}
              value={price}
              onChange={(e) => {
                setPrice(e.target.value);
                setError(null);
              }}
            />
          </span>
        </label>
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>
          <Icon name="bell" size={14} /> {saving ? "Creating…" : "Create watch"}
        </button>
      </div>
      <p className={`small watch-pop-hint${error ? " we-error" : " muted"}`} role={error ? "alert" : undefined}>
        {error ?? `Latest best price: ${formatPrice(latest.minPrice, latest.currency)} — the suggestion is about 10% under it.`}
      </p>
    </form>
  );
}
