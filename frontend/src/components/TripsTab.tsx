import { useCallback, useEffect, useState } from "react";
import { HttpError } from "../api";
import { CURRENCIES, formatPrice, formatTripDate, plural, stopsLabel, timeAgo } from "../lib/format";
import {
  bagsSummary,
  bookedFromDeal,
  cheapestId,
  dealTotal,
  EMPTY_BOOKED,
  EMPTY_TRIP,
  freshness,
  partyLabel,
  sortTrips,
  STATUS_LABELS,
  tripDates,
  tripIdFromHash,
  tripPriceLine,
  tripRoute,
} from "../lib/trips";
import type { BookedFlight, DealWithBags, Trip, TripDetail, TripFields, TripStatus } from "../lib/trips";
import { scoreTone } from "../lib/watches";
import type { StoredDeal } from "../types";
import { clearTripBooked, deleteTrip, fetchTrip, markTripBooked, removeTripItem, updateTrip, updateTripItem } from "../tripsApi";
import Icon from "./Icon";
import Segmented from "./Segmented";
import { useToast } from "./Toasts";
import { useTrips } from "./TripsContext";
import "./trips.css";

type Deal = StoredDeal & DealWithBags;

function openTrip(id: number | null) {
  window.location.hash = id == null ? "trips" : `trips/${id}`; // a history entry, so Back returns to the list
  window.scrollTo({ top: 0 });
}

function StatusChip({ status }: { status: TripStatus }) {
  return <span className={`badge trip-status trip-status-${status}`}>{STATUS_LABELS[status]}</span>;
}

// --- the plan form (new trip / edit) ----------------------------------------------

function TripForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: TripFields;
  submitLabel: string;
  onSubmit: (fields: TripFields) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [f, setF] = useState<TripFields>(initial);
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<TripFields>) => setF((prev) => ({ ...prev, ...patch }));
  const pax = (key: "adults" | "children" | "infants", value: string) =>
    set({ passengers: { ...f.passengers, [key]: Math.max(key === "adults" ? 1 : 0, Number(value) || 0) } });

  return (
    <form
      className="trip-form card"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!f.name.trim()) return;
        setSaving(true);
        const ok = await onSubmit({ ...f, name: f.name.trim() });
        setSaving(false);
        if (ok) onCancel();
      }}
    >
      <label className="field trip-form-name">
        <span className="field-label">Trip name</span>
        <input value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Rome, Hanukkah" maxLength={120} required autoFocus />
      </label>
      <div className="trip-form-row">
        <label className="field">
          <span className="field-label">From</span>
          <input value={f.origin} onChange={(e) => set({ origin: e.target.value })} placeholder="TLV" />
        </label>
        <label className="field">
          <span className="field-label">To</span>
          <input value={f.destination} onChange={(e) => set({ destination: e.target.value })} placeholder="Rome" />
        </label>
      </div>
      <div className="trip-form-row">
        <label className="field">
          <span className="field-label">When (free text)</span>
          <input value={f.dates_hint} onChange={(e) => set({ dates_hint: e.target.value })} placeholder="Hanukkah week" />
        </label>
        <label className="field">
          <span className="field-label">Earliest departure</span>
          <input type="date" value={f.depart_from ?? ""} onChange={(e) => set({ depart_from: e.target.value || null })} />
        </label>
        <label className="field">
          <span className="field-label">Latest departure</span>
          <input type="date" value={f.depart_to ?? ""} onChange={(e) => set({ depart_to: e.target.value || null })} />
        </label>
      </div>
      <div className="trip-form-row trip-form-pax">
        <label className="field">
          <span className="field-label">Adults</span>
          <input type="number" min={1} max={9} value={f.passengers.adults} onChange={(e) => pax("adults", e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Children</span>
          <input type="number" min={0} max={8} value={f.passengers.children} onChange={(e) => pax("children", e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Infants</span>
          <input type="number" min={0} max={4} value={f.passengers.infants} onChange={(e) => pax("infants", e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Currency</span>
          <select value={f.currency} onChange={(e) => set({ currency: e.target.value })}>
            {CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="trip-form-actions">
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving || !f.name.trim()}>
          {submitLabel}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// --- list -----------------------------------------------------------------------

function TripCard({ trip }: { trip: Trip }) {
  const route = tripRoute(trip);
  const dates = tripDates(trip);
  const price = tripPriceLine(trip);
  return (
    <a
      className="trip-card card"
      href={`#trips/${trip.id}`}
      onClick={(e) => {
        e.preventDefault();
        openTrip(trip.id);
      }}
    >
      <div className="trip-card-head">
        <h3 className="trip-name">{trip.name}</h3>
        <StatusChip status={trip.status} />
      </div>
      <p className="muted small">
        {[route, dates, partyLabel(trip.passengers)].filter(Boolean).join(" · ")}
      </p>
      <div className="trip-card-foot">
        {price ? (
          <span className="trip-price">
            <span className="muted small">{price.label} </span>
            <strong>{price.price}</strong>
          </span>
        ) : (
          <span className="muted small">No prices yet</span>
        )}
        <span className="muted small">{trip.items ? plural(trip.items, "item") : "Nothing linked"}</span>
      </div>
    </a>
  );
}

// --- detail: shortlist compare table -------------------------------------------------

function bestLink(d: StoredDeal): { label: string; url: string } | null {
  const link = d.booking_links.find((l) => l.url);
  if (link) return { label: link.label, url: link.url };
  return d.booking_url ? { label: "Booking page", url: d.booking_url } : null;
}

function Times({ date, dep, arr }: { date: string; dep: string; arr: string }) {
  return (
    <>
      <span>{formatTripDate(date)}</span>
      {(dep || arr) && (
        <span className="muted small">
          {dep || "?"}–{arr || "?"}
        </span>
      )}
    </>
  );
}

function Shortlist({
  trip,
  onPin,
  onRemove,
  onBook,
}: {
  trip: TripDetail;
  onPin: (itemId: number, pinned: boolean) => void;
  onRemove: (itemId: number) => void;
  onBook: (deal: Deal) => void;
}) {
  const cols = trip.deals.filter((x) => x.deal) as { item: TripDetail["deals"][number]["item"]; deal: Deal }[];
  const gone = trip.deals.length - cols.length;
  const cheapest = cheapestId(cols.map((c) => c.deal));
  if (!trip.deals.length) {
    return (
      <p className="muted small trip-empty-note">
        Nothing shortlisted yet. Tap <strong>Add to trip</strong> on any flight in Search or History to compare it here.
      </p>
    );
  }
  const rows: { label: string; cell: (d: Deal) => React.ReactNode }[] = [
    { label: "Out", cell: (d) => <Times date={d.depart_date} dep={d.depart_time} arr={d.arrive_time} /> },
    { label: "Back", cell: (d) => (d.return_date ? <Times date={d.return_date} dep={d.return_depart_time} arr="" /> : <span className="muted">One way</span>) },
    { label: "Stops", cell: (d) => <span className={d.stops === 0 ? "text-good" : ""}>{stopsLabel(d.stops)}{d.duration ? ` · ${d.duration}` : ""}</span> },
    { label: "Fare", cell: (d) => formatPrice(d.price, d.currency) },
    {
      label: "Bags",
      cell: (d) => {
        const b = bagsSummary(d);
        return b ? <span title={b.tip}>{b.text}</span> : <span className="muted">None asked</span>;
      },
    },
    {
      label: "Total with bags",
      cell: (d) => {
        const t = dealTotal(d);
        return <strong className={d.id === cheapest ? "text-good" : ""}>{formatPrice(t.price, t.currency)}</strong>;
      },
    },
    {
      label: "Freshness",
      cell: (d) => {
        const f = freshness(d);
        return (
          <span className={`trip-fresh trip-fresh-${f}`} title={f === "stale" ? "Prices move — check again before booking" : undefined}>
            {timeAgo(d.seen_at || d.found_at)}
          </span>
        );
      },
    },
  ];
  return (
    <>
      <div className="compare-wrap" role="region" aria-label="Shortlist comparison" tabIndex={0}>
        <table className="compare">
          <thead>
            <tr>
              <th scope="row" className="compare-label">
                <span className="sr-only">Flight</span>
              </th>
              {cols.map(({ item, deal: d }) => (
                <th key={item.id} scope="col" className={d.id === cheapest ? "compare-best" : ""}>
                  <div className="compare-head">
                    <span className="compare-airline">{d.airline || "Airline n/a"}</span>
                    <button
                      type="button"
                      className={`icon-btn compare-pin${item.pinned ? " pinned" : ""}`}
                      onClick={() => onPin(item.id, !item.pinned)}
                      aria-pressed={item.pinned}
                      title={item.pinned ? "Unpin" : "Pin as a favourite (listed first)"}
                      aria-label={item.pinned ? "Unpin" : "Pin"}
                    >
                      <Icon name="star" size={14} />
                    </button>
                  </div>
                  <span className="muted small">{d.flight_numbers || `${d.origin_airport || d.origin} → ${d.destination_airport || d.destination}`}</span>
                  {d.id === cheapest && <span className="badge badge-good compare-tag">Cheapest</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <th scope="row" className="compare-label">
                  {r.label}
                </th>
                {cols.map(({ item, deal: d }) => (
                  <td key={item.id} className={d.id === cheapest ? "compare-best" : ""}>
                    <div className="compare-cell">{r.cell(d)}</div>
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <th scope="row" className="compare-label">
                Book
              </th>
              {cols.map(({ item, deal: d }) => {
                const link = bestLink(d);
                return (
                  <td key={item.id} className={d.id === cheapest ? "compare-best" : ""}>
                    <div className="compare-cell compare-actions">
                      {link && (
                        <a className="btn btn-secondary btn-sm" href={link.url} target="_blank" rel="noreferrer">
                          {link.label} <Icon name="external" size={12} />
                        </a>
                      )}
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => onBook(d)}>
                        <Icon name="check" size={13} /> Booked it
                      </button>
                      <button type="button" className="btn btn-ghost btn-sm btn-danger" onClick={() => onRemove(item.id)} aria-label={`Remove ${d.airline} from the shortlist`}>
                        <Icon name="x" size={13} /> Remove
                      </button>
                    </div>
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </div>
      {gone > 0 && <p className="muted small">{plural(gone, "shortlisted flight")} no longer stored.</p>}
    </>
  );
}

// --- detail: booked flight -------------------------------------------------------------

function BookedForm({ initial, onSave, onCancel }: { initial: BookedFlight; onSave: (b: BookedFlight) => Promise<boolean>; onCancel: () => void }) {
  const [b, setB] = useState<BookedFlight>(initial);
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<BookedFlight>) => setB((prev) => ({ ...prev, ...patch }));
  return (
    <form
      className="trip-form"
      onSubmit={async (e) => {
        e.preventDefault();
        setSaving(true);
        const ok = await onSave(b);
        setSaving(false);
        if (ok) onCancel();
      }}
    >
      <div className="trip-form-row">
        <label className="field">
          <span className="field-label">Airline</span>
          <input value={b.airline} onChange={(e) => set({ airline: e.target.value })} placeholder="El Al" />
        </label>
        <label className="field">
          <span className="field-label">Flight numbers</span>
          <input value={b.flight_numbers} onChange={(e) => set({ flight_numbers: e.target.value })} placeholder="LY 381, LY 382" />
        </label>
      </div>
      <div className="trip-form-row">
        <label className="field">
          <span className="field-label">Depart</span>
          <input type="date" value={b.depart_date} onChange={(e) => set({ depart_date: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">Return</span>
          <input type="date" value={b.return_date} onChange={(e) => set({ return_date: e.target.value })} />
        </label>
      </div>
      <div className="trip-form-row">
        <label className="field">
          <span className="field-label">Price paid (whole party)</span>
          <input
            type="number"
            min={0}
            step="any"
            value={b.price ?? ""}
            onChange={(e) => set({ price: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </label>
        <label className="field">
          <span className="field-label">Currency</span>
          <select value={b.currency} onChange={(e) => set({ currency: e.target.value })}>
            {[...new Set([...CURRENCIES, b.currency])].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Booking ref</span>
          <input value={b.booking_ref} onChange={(e) => set({ booking_ref: e.target.value })} placeholder="ABC123" />
        </label>
      </div>
      <label className="field">
        <span className="field-label">Notes</span>
        <input value={b.notes} onChange={(e) => set({ notes: e.target.value })} placeholder="Seats, bags bought, check-in opens…" maxLength={2000} />
      </label>
      <div className="trip-form-actions">
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>
          Save booking
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function BookedPanel({
  trip,
  draft,
  onDraft,
  onSave,
  onClear,
}: {
  trip: TripDetail;
  draft: BookedFlight | null; // the open form's starting values, or null when closed
  onDraft: (b: BookedFlight | null) => void;
  onSave: (b: BookedFlight) => Promise<boolean>;
  onClear: () => void;
}) {
  const b = trip.booked;
  if (draft) return <BookedForm key={JSON.stringify(draft)} initial={draft} onSave={onSave} onCancel={() => onDraft(null)} />;
  if (!b) {
    return (
      <div className="trip-booked-empty">
        <p className="muted small">Bought the tickets? Record the flight here — from a shortlisted fare (“Booked it”) or by hand.</p>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => onDraft({ ...EMPTY_BOOKED, currency: trip.currency })}>
          <Icon name="check" size={14} /> Mark booked
        </button>
      </div>
    );
  }
  const flight = [b.airline, b.flight_numbers].filter(Boolean).join(" · ");
  const dates = [b.depart_date, b.return_date].filter(Boolean).map(formatTripDate).join(" – ");
  return (
    <div className="trip-booked">
      <div>
        <strong>{flight || "Booked flight"}</strong>
        {dates && <p className="small">{dates}</p>}
        <p className="muted small">
          {[b.price != null ? `Paid ${formatPrice(b.price, b.currency)}` : "", b.booking_ref ? `Ref ${b.booking_ref}` : "", b.booked_at ? `recorded ${timeAgo(b.booked_at)}` : ""]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {b.notes && <p className="small">{b.notes}</p>}
      </div>
      <div className="trip-booked-actions">
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => onDraft({ ...EMPTY_BOOKED, ...b })}>
          <Icon name="edit" size={13} /> Edit
        </button>
        <button type="button" className="btn btn-ghost btn-sm btn-danger" onClick={onClear}>
          <Icon name="x" size={13} /> Not booked
        </button>
      </div>
    </div>
  );
}

// --- detail: linked searches and watches -----------------------------------------------

function SearchItems({
  trip,
  busy,
  onRerun,
  onRemove,
}: {
  trip: TripDetail;
  busy: boolean;
  onRerun: (deal: StoredDeal) => void;
  onRemove: (itemId: number) => void;
}) {
  if (!trip.searches.length) {
    return <p className="muted small trip-empty-note">No searches linked. Use the suitcase on a History card to follow a search here.</p>;
  }
  return (
    <div className="trip-links">
      {trip.searches.map((s) => {
        const deals = s.deals as Deal[];
        const best = deals[0];
        const total = best ? dealTotal(best) : null;
        return (
          <article key={s.item.id} className="trip-link card">
            <div className="trip-link-head">
              <div>
                <strong>{(s.route || s.item.search_query).replace(" -> ", " → ")}</strong>
                <p className="muted small">{s.item.search_query.split(" (")[1]?.replace(/\)$/, "") ?? ""}</p>
              </div>
              {total && (
                <span className="trip-price">
                  <span className="muted small">best </span>
                  <strong>{formatPrice(total.price, total.currency)}</strong>
                </span>
              )}
            </div>
            <p className="muted small">
              {s.runs ? `${plural(s.runs, "run")} · latest ${s.latest_run_at ? timeAgo(s.latest_run_at) : "—"}` : "No stored runs"}
            </p>
            {deals.length > 0 && (
              <ul className="trip-mini-deals">
                {deals.slice(0, 3).map((d) => {
                  const t = dealTotal(d);
                  return (
                    <li key={d.id}>
                      <span>
                        {d.airline || "Airline n/a"} · {formatTripDate(d.depart_date)}
                        {d.return_date && ` – ${formatTripDate(d.return_date)}`} · {stopsLabel(d.stops)}
                      </span>
                      <strong>{formatPrice(t.price, t.currency)}</strong>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="trip-link-actions">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={busy || !best}
                onClick={() => best && onRerun(best)}
                title="Run this search again with the same criteria"
              >
                <Icon name="refresh" size={14} /> Re-run
              </button>
              <button type="button" className="btn btn-ghost btn-sm btn-danger push" onClick={() => onRemove(s.item.id)} aria-label={`Unlink ${s.route}`}>
                <Icon name="x" size={13} /> Unlink
              </button>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function WatchItems({
  trip,
  busy,
  onRun,
  onOpen,
  onRemove,
}: {
  trip: TripDetail;
  busy: boolean;
  onRun: (id: number) => void;
  onOpen: (id: number) => void;
  onRemove: (itemId: number) => void;
}) {
  if (!trip.watches.length) {
    return <p className="muted small trip-empty-note">No watches linked. Use “Add to trip” on a watch to track it here.</p>;
  }
  return (
    <div className="trip-links">
      {trip.watches.map(({ item, watch: w }) => {
        if (!w) {
          return (
            <article key={item.id} className="trip-link card">
              <div className="trip-link-head">
                <span className="muted">A watch that was deleted</span>
                <button type="button" className="btn btn-ghost btn-sm btn-danger" onClick={() => onRemove(item.id)}>
                  <Icon name="x" size={13} /> Unlink
                </button>
              </div>
            </article>
          );
        }
        const last = w.stats?.last_best ?? w.last_best_price;
        const tone = w.stats?.score != null ? scoreTone(w.stats.score) : null;
        return (
          <article key={item.id} className="trip-link card">
            <div className="trip-link-head">
              <div>
                <strong>
                  {w.origin} → {w.destination}
                </strong>
                <p className="muted small">
                  {w.dates_hint} · alert at ≤ {formatPrice(w.max_price, w.currency)}
                  {!w.active && " · paused"}
                </p>
              </div>
              {last != null && (
                <span className="trip-price">
                  <span className="muted small">last </span>
                  <strong>{formatPrice(last, w.currency)}</strong>
                </span>
              )}
            </div>
            <p className="muted small">
              {w.last_run_at ? `Checked ${timeAgo(w.last_run_at)}` : "Not checked yet"}
              {w.stats?.low_30d != null && ` · 30-day low ${formatPrice(w.stats.low_30d, w.currency)}`}
              {tone && ` · score ${w.stats!.score} (${tone.label})`}
            </p>
            <div className="trip-link-actions">
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy || !w.active} onClick={() => onRun(w.id)}>
                <Icon name="play" size={14} /> Run now
              </button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpen(w.id)}>
                <Icon name="bell" size={14} /> Open
              </button>
              <button type="button" className="btn btn-ghost btn-sm btn-danger push" onClick={() => onRemove(item.id)} aria-label={`Unlink watch ${w.origin} to ${w.destination}`}>
                <Icon name="x" size={13} /> Unlink
              </button>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function Notes({ value, onSave }: { value: string; onSave: (notes: string) => Promise<boolean> }) {
  const [text, setText] = useState(value);
  const [saved, setSaved] = useState(value);
  useEffect(() => {
    setText(value);
    setSaved(value);
  }, [value]);
  const dirty = text !== saved;
  const save = async () => {
    if (dirty && (await onSave(text))) setSaved(text);
  };
  return (
    <div className="trip-notes">
      <label className="sr-only" htmlFor="trip-notes">
        Notes
      </label>
      <textarea
        id="trip-notes"
        className="input"
        rows={4}
        value={text}
        maxLength={5000}
        onChange={(e) => setText(e.target.value)}
        onBlur={save}
        placeholder="Hotel ideas, school holidays, who's coming, passport expiry…"
      />
      {dirty && (
        <button type="button" className="btn btn-secondary btn-sm" onClick={save}>
          Save notes
        </button>
      )}
    </div>
  );
}

// --- detail view ----------------------------------------------------------------------

function TripView({
  id,
  busy,
  onRerunSearch,
  onRunWatch,
  onOpenWatch,
  onError,
}: {
  id: number;
  busy: boolean;
  onRerunSearch: (deal: StoredDeal) => void;
  onRunWatch: (id: number) => void;
  onOpenWatch: (id: number) => void;
  onError: (e: unknown) => void;
}) {
  const toast = useToast();
  const ctx = useTrips();
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [bookedDraft, setBookedDraft] = useState<BookedFlight | null>(null);

  const load = useCallback(async () => {
    try {
      setTrip(await fetchTrip(id));
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) setMissing(true);
      else onError(e);
    }
  }, [id, onError]);

  useEffect(() => {
    load();
  }, [load]);

  /** Run a change, then reload this trip and the shared list; true on success. */
  async function act(fn: () => Promise<unknown>, done?: string): Promise<boolean> {
    try {
      await fn();
      if (done) toast("success", done);
      await load();
      ctx?.refresh();
      return true;
    } catch (e) {
      onError(e);
      return false;
    }
  }

  if (missing) {
    return (
      <div className="empty card">
        <Icon name="suitcase" size={32} />
        <strong>This trip doesn't exist anymore</strong>
        <button className="btn btn-secondary" onClick={() => openTrip(null)}>
          All trips
        </button>
      </div>
    );
  }
  if (!trip) {
    return (
      <div className="tab-trips">
        <div className="skeleton skeleton-bar" />
        <div className="skeleton skeleton-card tall" />
      </div>
    );
  }

  const fields: TripFields = { ...EMPTY_TRIP, ...trip };
  const price = tripPriceLine(trip);
  const unbook = () => {
    if (window.confirm("Forget the booked flight? The trip goes back to planning.")) act(() => clearTripBooked(trip.id), "Booking cleared.");
  };
  const remove = async () => {
    if (!window.confirm(`Delete the trip “${trip.name}”? Its searches, watches and deals stay — only the trip goes.`)) return;
    if (await act(() => deleteTrip(trip.id), `Deleted ${trip.name}.`)) openTrip(null);
  };

  return (
    <div className="tab-trips trip-view">
      <a
        className="btn-link small trip-back"
        href="#trips"
        onClick={(e) => {
          e.preventDefault();
          openTrip(null);
        }}
      >
        <Icon name="chevronRight" size={14} className="flip" /> All trips
      </a>

      {editing ? (
        <TripForm
          initial={fields}
          submitLabel="Save trip"
          onSubmit={(f) => act(() => updateTrip(trip.id, f), "Trip saved.")}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <div className="page-head trip-head">
          <div>
            <h2>{trip.name}</h2>
            <p className="muted small">{[tripRoute(trip), tripDates(trip), partyLabel(trip.passengers)].filter(Boolean).join(" · ")}</p>
          </div>
          <div className="trip-head-actions">
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}>
              <Icon name="edit" size={14} /> Edit
            </button>
            <button className="btn btn-ghost btn-sm btn-danger" onClick={remove}>
              <Icon name="trash" size={14} /> Delete
            </button>
          </div>
        </div>
      )}

      <div className="trip-summary card">
        <Segmented
          label="Trip status"
          size="sm"
          value={trip.status}
          onChange={(status) => act(() => updateTrip(trip.id, { status }))}
          options={(["planning", "booked", "done"] as TripStatus[]).map((s) => ({ value: s, label: STATUS_LABELS[s] }))}
        />
        {price && (
          <span className="trip-price trip-price-lg" title={trip.best && !trip.booked ? `From ${trip.best.label}` : undefined}>
            <span className="muted small">{price.label} </span>
            <strong>{price.price}</strong>
          </span>
        )}
      </div>

      <section className="trip-section" aria-labelledby="trip-booked-h">
        <h3 id="trip-booked-h" className="section-title">
          Booked flight
        </h3>
        <div className="card trip-section-body">
          <BookedPanel
            trip={trip}
            draft={bookedDraft}
            onDraft={setBookedDraft}
            onSave={(b) => act(() => markTripBooked(trip.id, b), "Marked as booked.")}
            onClear={unbook}
          />
        </div>
      </section>

      <section className="trip-section" aria-labelledby="trip-shortlist-h">
        <h3 id="trip-shortlist-h" className="section-title">
          Shortlist <span className="section-count">{trip.deals.length}</span>
        </h3>
        <Shortlist
          trip={trip}
          onPin={(itemId, pinned) => act(() => updateTripItem(trip.id, itemId, { pinned }))}
          onRemove={(itemId) => act(() => removeTripItem(trip.id, itemId), "Removed from the shortlist.")}
          onBook={(d) => {
            setBookedDraft(bookedFromDeal(d));
            document.getElementById("trip-booked-h")?.scrollIntoView({ behavior: "smooth" });
          }}
        />
      </section>

      <section className="trip-section" aria-labelledby="trip-searches-h">
        <h3 id="trip-searches-h" className="section-title">
          Searches <span className="section-count">{trip.searches.length}</span>
        </h3>
        <SearchItems trip={trip} busy={busy} onRerun={onRerunSearch} onRemove={(itemId) => act(() => removeTripItem(trip.id, itemId), "Search unlinked.")} />
      </section>

      <section className="trip-section" aria-labelledby="trip-watches-h">
        <h3 id="trip-watches-h" className="section-title">
          Watches <span className="section-count">{trip.watches.length}</span>
        </h3>
        <WatchItems
          trip={trip}
          busy={busy}
          onRun={onRunWatch}
          onOpen={onOpenWatch}
          onRemove={(itemId) => act(() => removeTripItem(trip.id, itemId), "Watch unlinked.")}
        />
      </section>

      <section className="trip-section" aria-labelledby="trip-notes-h">
        <h3 id="trip-notes-h" className="section-title">
          Notes
        </h3>
        <Notes value={trip.notes} onSave={(notes) => act(() => updateTrip(trip.id, { notes }))} />
      </section>
    </div>
  );
}

// --- the tab ---------------------------------------------------------------------------

export default function TripsTab({
  busy,
  onRerunSearch,
  onRunWatch,
  onOpenWatch,
  onError,
}: {
  busy: boolean;
  onRerunSearch: (deal: StoredDeal) => void;
  onRunWatch: (id: number) => void;
  onOpenWatch: (id: number) => void;
  onError: (e: unknown) => void;
}) {
  const ctx = useTrips();
  const [openId, setOpenId] = useState<number | null>(() => tripIdFromHash(window.location.hash));
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const onHash = () => setOpenId(tripIdFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // The list is re-fetched on every visit: best prices move with each run.
  const refresh = ctx?.refresh;
  useEffect(() => {
    if (openId == null) refresh?.();
  }, [openId, refresh]);

  if (!ctx) return null;
  if (openId != null) {
    return <TripView key={openId} id={openId} busy={busy} onRerunSearch={onRerunSearch} onRunWatch={onRunWatch} onOpenWatch={onOpenWatch} onError={onError} />;
  }

  const trips = sortTrips(ctx.trips);
  return (
    <div className="tab-trips">
      <div className="page-head">
        <div>
          <h2>Trips</h2>
          <p className="muted small">Plan a family trip: shortlist flights side by side, follow its searches and watches, and keep the booking in one place.</p>
        </div>
        {!creating && (
          <button className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>
            <Icon name="plus" size={15} /> New trip
          </button>
        )}
      </div>
      {creating && (
        <TripForm
          initial={EMPTY_TRIP}
          submitLabel="Create trip"
          onSubmit={async (f) => {
            const trip = await ctx.create(f);
            if (trip) openTrip(trip.id);
            return !!trip;
          }}
          onCancel={() => setCreating(false)}
        />
      )}
      {!ctx.loaded ? (
        <div className="trip-grid">
          {Array.from({ length: 2 }, (_, i) => (
            <div key={i} className="skeleton skeleton-card" />
          ))}
        </div>
      ) : trips.length === 0 && !creating ? (
        <div className="empty card">
          <Icon name="suitcase" size={32} />
          <strong>No trips yet</strong>
          <span className="muted">
            Start a trip for the next family holiday, then tap <strong>Add to trip</strong> on flights, History cards and watches to collect them here.
          </span>
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            <Icon name="plus" size={16} /> Plan a trip
          </button>
        </div>
      ) : (
        <div className="trip-grid">
          {trips.map((t) => (
            <TripCard key={t.id} trip={t} />
          ))}
        </div>
      )}
    </div>
  );
}
