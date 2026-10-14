import { useEffect, useId, useRef, useState } from "react";
import { STATUS_LABELS, sortTrips, tripRoute } from "../lib/trips";
import type { TripFields, TripItemCreate } from "../lib/trips";
import Icon from "./Icon";
import { useTrips } from "./TripsContext";
import "./trips.css";

/** "Add to trip ▾": a small menu of trips plus "New trip…", on deal cards
 *  (shortlist), History cards (the search) and watches. Renders nothing
 *  outside a TripsProvider. */
export default function AddToTrip({
  item,
  defaults,
  compact = false,
}: {
  item: TripItemCreate;
  defaults?: () => Partial<TripFields>; // a new trip's pre-filled fields (computed on demand)
  compact?: boolean; // icon-only button (History card head)
}) {
  const ctx = useTrips();
  const [open, setOpen] = useState(false);
  const [naming, setNaming] = useState<string | null>(null); // the "New trip…" name field, when shown
  const [saving, setSaving] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const uid = useId();

  function close() {
    setOpen(false);
    setNaming(null);
  }

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) close();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  if (!ctx) return null;
  const { trips, loaded, ensureLoaded, addTo, create } = ctx;

  function toggle() {
    if (!open) ensureLoaded();
    setOpen(!open);
    setNaming(null);
  }

  async function pick(trip: { id: number; name: string }) {
    setSaving(true);
    const ok = await addTo(trip, item);
    setSaving(false);
    if (ok) close();
  }

  async function createNew() {
    const name = (naming ?? "").trim();
    if (!name) return;
    setSaving(true);
    const trip = await create({ ...(defaults?.() ?? {}), name }, item);
    setSaving(false);
    if (trip) close();
  }

  const what = item.kind === "deal" ? "this flight" : item.kind === "watch" ? "this watch" : "this search";
  const list = sortTrips(trips).filter((t) => t.status !== "done");

  return (
    <div className={`att ${compact ? "att-pop" : "att-inline"}${open ? " open" : ""}`} ref={root} onKeyDown={(e) => e.key === "Escape" && close()}>
      <button
        type="button"
        className={compact ? "icon-btn att-btn" : "btn btn-ghost btn-sm att-btn"}
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-controls={open ? `${uid}-menu` : undefined}
        title={`Add ${what} to a trip`}
        aria-label={compact ? `Add ${what} to a trip` : undefined}
      >
        <Icon name="suitcase" size={compact ? 17 : 14} />
        {!compact && (
          <>
            Add to trip <Icon name="chevronDown" size={13} />
          </>
        )}
      </button>
      {open && (
        <div className="att-menu card" id={`${uid}-menu`} role="menu" aria-label="Add to trip">
          {!loaded && <p className="muted small att-empty">Loading trips…</p>}
          {loaded && list.length === 0 && naming === null && <p className="muted small att-empty">No trips yet — start one:</p>}
          {list.map((t) => (
            <button key={t.id} type="button" role="menuitem" className="att-item" disabled={saving} onClick={() => pick(t)}>
              <span className="att-item-name">{t.name}</span>
              <span className="muted small">
                {tripRoute(t) || "No route yet"}
                {t.status !== "planning" && ` · ${STATUS_LABELS[t.status]}`}
              </span>
            </button>
          ))}
          {naming === null ? (
            <button
              type="button"
              role="menuitem"
              className="att-item att-new"
              onClick={() => setNaming(defaults?.().name ?? "")}
              disabled={saving}
            >
              <Icon name="plus" size={14} /> New trip…
            </button>
          ) : (
            <form
              className="att-form"
              onSubmit={(e) => {
                e.preventDefault();
                createNew();
              }}
            >
              <label className="sr-only" htmlFor={`${uid}-name`}>
                Trip name
              </label>
              <input
                id={`${uid}-name`}
                className="input"
                value={naming}
                onChange={(e) => setNaming(e.target.value)}
                placeholder="e.g. Rome, Hanukkah"
                maxLength={120}
                autoFocus
              />
              <button type="submit" className="btn btn-primary btn-sm" disabled={saving || !naming.trim()}>
                Create
              </button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
