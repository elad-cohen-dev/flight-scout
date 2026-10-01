import { useEffect, useState } from "react";
import { formatPrice, timeAgo } from "../lib/format";
import { criteriaChips } from "../lib/search";
import { alertSettingsOf, describeWatch, ruleChips, ruleHeadline, ruleWords, scoreTone } from "../lib/watches";
import type { Watch, WatchUpdate } from "../types";
import Icon from "./Icon";
import WatchEditor from "./WatchEditor";
import "./watches.css";
import "./alerts.css";

/** Last best / 30-day low / typical / deal score, from the API's per-watch stats. */
function WatchStatsGrid({ watch: w, hit }: { watch: Watch; hit: boolean }) {
  const s = w.stats ?? null;
  const price = (n: number | null | undefined) => (n != null ? formatPrice(n, w.currency) : "—");
  const last = s?.last_best ?? w.last_best_price;
  const tone = s?.score != null ? scoreTone(s.score) : null;
  const everLower = s?.all_time_low != null && s.low_30d != null && s.all_time_low < s.low_30d;
  return (
    <div className="watch-stats ws-grid watch-dim">
      <div>
        <span className="muted small">Last best</span>
        <strong className={hit ? "text-good" : ""}>{price(last)}</strong>
        <span className="ws-note">{w.last_run_at ? timeAgo(w.last_run_at) : "Not checked yet"}</span>
      </div>
      <div>
        <span className="muted small">30-day low</span>
        <strong>{price(s?.low_30d)}</strong>
        <span className="ws-note">{everLower ? `${price(s!.all_time_low)} all-time` : s ? "lowest so far" : "\u00a0"}</span>
      </div>
      <div>
        <span className="muted small">Typical</span>
        <strong>{price(s?.typical)}</strong>
        <span className="ws-note">{s ? `${s.runs} check${s.runs === 1 ? "" : "s"}` : "\u00a0"}</span>
      </div>
      <div>
        <span className="muted small">Deal score</span>
        {s?.score != null && tone ? (
          <strong className={`score-pill score-${tone.tone}`} title="How this price compares with every earlier check of this watch">
            {s.score}
            <small>/100</small>
          </strong>
        ) : (
          <strong>—</strong>
        )}
        <span className="ws-note">{tone ? tone.label : "after 4 checks"}</span>
      </div>
    </div>
  );
}

/** "#watches/12" deep-links to watch 12's editor (History's "Edit watch" uses it). */
function editIdFromHash(): number | null {
  const m = window.location.hash.match(/^#\/?watches\/(\d+)/);
  return m ? Number(m[1]) : null;
}

function setEditHash(id: number | null) {
  // replaceState: opening/closing an editor shouldn't pile up back-button entries
  history.replaceState(null, "", id == null ? "#watches" : `#watches/${id}`);
}

function WatchCard({
  watch: w,
  editing,
  onEdit,
  onCloseEditor,
  onSave,
  onToggle,
  onRunNow,
  onDelete,
  busy,
}: {
  watch: Watch;
  editing: boolean;
  onEdit: () => void;
  onCloseEditor: () => void;
  onSave: (patch: WatchUpdate) => Promise<boolean>;
  onToggle: () => void;
  onRunNow: (id: number) => void;
  onDelete: (id: number) => void;
  busy: boolean;
}) {
  const chips = criteriaChips(w);
  const words = describeWatch(w);
  const alerts = alertSettingsOf(w);
  const rules = ruleChips(alerts);
  const hit = w.active && alerts.threshold && w.last_best_price != null && w.last_best_price <= w.max_price;
  // how far the last best price sits above the target, capped for the meter
  const ratio = w.last_best_price != null ? Math.min(1, w.max_price / w.last_best_price) : 0;
  const route = `${w.origin} to ${w.destination}`;
  return (
    <article className={`watch-card card${hit ? " watch-hit" : ""}${w.active ? "" : " watch-paused"}${editing ? " editing" : ""}`} id={`watch-${w.id}`}>
      <div className="watch-head">
        <div className="watch-dim">
          <h3 className="watch-route">
            {w.origin} → {w.destination}
          </h3>
          <p className="muted small">{w.dates_hint}</p>
        </div>
        {w.active ? (
          <span className="badge badge-good">Active</span>
        ) : (
          <span className="badge badge-paused" title="Skipped by the daily run until you resume it">
            <Icon name="pause" size={11} /> Paused
          </span>
        )}
      </div>
      {editing ? (
        <WatchEditor watch={w} onSave={onSave} onCancel={onCloseEditor} />
      ) : (
        <>
          <p className="watch-words small watch-dim">
            {words.search} {w.active ? <strong>{alerts.threshold ? words.alert : "Smart alerts only."}</strong> : <strong>Paused — not checked daily until you resume it.</strong>}
          </p>
          {chips.length > 0 && (
            <div className="chip-row watch-dim">
              {chips.map((c) => (
                <span key={c} className="badge">
                  {c}
                </span>
              ))}
            </div>
          )}
          <WatchStatsGrid watch={w} hit={hit} />
          {rules.length > 0 && (
            <div className="watch-meta small watch-dim" aria-label="Also alerts on">
              <span>Also alerts on</span>
              {rules.map((r) => (
                <span key={r} className="rule-chip">
                  {r}
                </span>
              ))}
            </div>
          )}
          {w.last_alert && (
            <p className="last-alert small watch-dim" title={ruleWords(w.last_alert.rule)}>
              <Icon name="bell" size={14} />
              <span>
                Alerted {timeAgo(w.last_alert.sent_at)} · <strong>{formatPrice(w.last_alert.price, w.last_alert.currency)}</strong> · {ruleHeadline(w.last_alert.rule)}
              </span>
            </p>
          )}
          {w.last_best_price != null && (
            <div className="meter watch-dim" title={hit ? "Last run found a price under your target" : "How close the last best price is to your target"}>
              <span style={{ width: `${Math.round(ratio * 100)}%` }} />
            </div>
          )}
          <div className="watch-actions">
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => onRunNow(w.id)}
              disabled={busy || !w.active}
              title={w.active ? "Re-search this route now (same check the daily run does)" : "Resume the watch to run it"}
            >
              <Icon name="play" size={14} /> Run now
            </button>
            <button className="btn btn-ghost btn-sm" onClick={onEdit} aria-label={`Edit watch ${route}`}>
              <Icon name="edit" size={14} /> Edit
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={onToggle}
              aria-label={`${w.active ? "Pause" : "Resume"} watch ${route}`}
              title={w.active ? "Stop the daily check (and alerts) without losing the watch" : "Include this watch in the daily check again"}
            >
              <Icon name={w.active ? "pause" : "play"} size={14} /> {w.active ? "Pause" : "Resume"}
            </button>
            <button className="btn btn-ghost btn-sm btn-danger push" onClick={() => onDelete(w.id)} aria-label={`Delete watch ${route}`}>
              <Icon name="trash" size={14} /> Delete
            </button>
          </div>
        </>
      )}
    </article>
  );
}

export default function WatchesTab({
  watches,
  loading,
  onRunNow,
  onDelete,
  onUpdate,
  onCreate,
  busy,
}: {
  watches: Watch[];
  loading: boolean;
  onRunNow: (id: number) => void;
  onDelete: (id: number) => void;
  onUpdate: (id: number, patch: WatchUpdate) => Promise<boolean>;
  onCreate: () => void;
  busy: boolean;
}) {
  const [editingId, setEditingId] = useState<number | null>(editIdFromHash);

  useEffect(() => {
    const onHash = () => setEditingId(editIdFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function edit(id: number | null) {
    setEditingId(id);
    setEditHash(id);
  }

  const active = watches.filter((w) => w.active).length;
  const paused = watches.length - active;

  return (
    <div className="tab-watches">
      <div className="page-head">
        <div>
          <h2>Price watches</h2>
          <p className="muted small">
            Active watches are re-checked daily; you get an alert when a deal drops to your target.
            {paused > 0 && ` ${active} active · ${paused} paused.`}
          </p>
        </div>
        <button className="btn btn-primary btn-sm" onClick={onCreate}>
          <Icon name="plus" size={15} /> New watch
        </button>
      </div>
      {loading ? (
        <div className="watch-grid">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="skeleton skeleton-card tall" />
          ))}
        </div>
      ) : watches.length === 0 ? (
        <div className="empty card">
          <Icon name="bell" size={32} />
          <strong>No price watches</strong>
          <span className="muted">Set up a search with a max price, then tap “Watch this route” — or tap the bell on any History card. The agent will keep checking for you.</span>
          <button className="btn btn-primary" onClick={onCreate}>
            <Icon name="plus" size={16} /> Create a watch
          </button>
        </div>
      ) : (
        <div className="watch-grid">
          {watches.map((w) => (
            <WatchCard
              key={w.id}
              watch={w}
              editing={editingId === w.id}
              onEdit={() => edit(w.id)}
              onCloseEditor={() => edit(null)}
              onSave={async (patch) => {
                const ok = await onUpdate(w.id, patch);
                if (ok) edit(null);
                return ok;
              }}
              onToggle={() => onUpdate(w.id, { active: !w.active })}
              onRunNow={onRunNow}
              onDelete={onDelete}
              busy={busy}
            />
          ))}
        </div>
      )}
    </div>
  );
}
