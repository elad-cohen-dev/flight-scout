import { useEffect, useRef, useState } from "react";
import { PASSENGER_LIMITS, passengersLabel, totalPassengers } from "../lib/search";
import type { Passengers } from "../types";
import Icon from "./Icon";

function Stepper({
  label,
  hint,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="stepper">
      <div>
        <div className="stepper-label">{label}</div>
        <div className="muted small">{hint}</div>
      </div>
      <div className="stepper-controls">
        <button type="button" className="icon-btn round" onClick={() => onChange(value - 1)} disabled={value <= min} aria-label={`Fewer ${label.toLowerCase()}`}>
          <Icon name="minus" size={16} />
        </button>
        <span className="stepper-value" aria-live="polite">
          {value}
        </span>
        <button type="button" className="icon-btn round" onClick={() => onChange(value + 1)} disabled={value >= max} aria-label={`More ${label.toLowerCase()}`}>
          <Icon name="plus" size={16} />
        </button>
      </div>
    </div>
  );
}

/** Travellers popover, like a flight search engine's. */
export default function PassengersPicker({
  value,
  onChange,
}: {
  value: Passengers;
  onChange: (p: Passengers) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const total = totalPassengers(value);
  const room = PASSENGER_LIMITS.total - total;
  const set = (patch: Partial<Passengers>) => {
    const next = { ...value, ...patch };
    // an infant flies on an adult's lap
    next.infants = Math.min(next.infants, next.adults);
    onChange(next);
  };

  return (
    <div className="popover-anchor" ref={ref}>
      <button
        type="button"
        className="pill-select"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="users" size={16} />
        <span>{passengersLabel(value)}</span>
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Travellers">
          <Stepper label="Adults" hint="12+ years" value={value.adults} min={1} max={Math.min(PASSENGER_LIMITS.adults[1], value.adults + room)} onChange={(v) => set({ adults: v })} />
          <Stepper label="Children" hint="2–11 years" value={value.children} min={0} max={Math.min(PASSENGER_LIMITS.children[1], value.children + room)} onChange={(v) => set({ children: v })} />
          <Stepper
            label="Infants"
            hint="Under 2, on lap"
            value={value.infants}
            min={0}
            max={Math.min(PASSENGER_LIMITS.infants[1], value.adults, value.infants + room)}
            onChange={(v) => set({ infants: v })}
          />
          <div className="popover-foot">
            <span className="muted small">Max {PASSENGER_LIMITS.total} travellers</span>
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
