import { useEffect, useState } from "react";
import { fetchUsage } from "../api";
import { planLine, planLow, planTip, showAdvice } from "../lib/advice";
import { formatPrice, timeAgo } from "../lib/format";
import { usageWarning } from "../lib/providers";
import { criteriaChips } from "../lib/search";
import {
  alertSettingsOf,
  basisNote,
  describeWatch,
  legacyNote,
  legacyOnly,
  priceRange,
  ruleChips,
  ruleHeadline,
  ruleWords,
  scoreTone,
} from "../lib/watches";
import { tripFromWatch } from "../lib/trips";
import type { Watch, WatchUpdate } from "../types";
import AddToTrip from "./AddToTrip";
import AdviceChip from "./AdviceChip";
import Icon from "./Icon";
import WatchEditor from "./WatchEditor";
import "./watches.css";
import "./alerts.css";

/** A watch's numbers, from the API's per-watch stats. Numbers from unverified
 *  estimates only stay hidden behind a muted note. */
function watchNumbers(w: Watch) {
  const all = w.stats ?? null;
  const s = legacyOnly(all) ? null : all;
  const price = (n: number | null | undefined) => (n != null ? formatPrice(n, w.currency) : "—");
  const range = s?.typical_low != null && s.typical_high != null ? priceRange(s.typical_low, s.typical_high, w.currency) : null;
  return {
    s,
    price,
    range,
    last: s?.last_best ?? w.last_best_price,
    lastAt: s?.last_run_at ?? w.last_run_at,
    tone: s?.score != null ? scoreTone(s.score) : null,
    everLower: s?.all_time_low != null && s.low_30d != null && s.all_time_low < s.low_30d,
    basis: s ? basisNote(s) : "",
    legacy: all ? legacyNote(all, w.currency) : null,
  };
}

/** The headline: what the last check found, against the price you asked for. */
function WatchHeadline({ watch: w, hit, threshold }: { watch: Watch; hit: boolean; threshold: boolean }) {
  const { s, price, last, lastAt, tone } = watchNumbers(w);
  const gap = last != null && threshold ? last - w.max_price : null;
  return (
    <div className="watch-headline watch-dim">
      <div className="wh-cell">
        <span className="muted small">Last best</span>
        <strong className={`wh-price${hit ? " text-good" : ""}`}>{price(last)}</strong>
        <span className="ws-note">{lastAt ? timeAgo(lastAt) : "Not checked yet"}</span>
      </div>
      <div className="wh-cell">
        <span className="muted small">Your target</span>
        <strong className="wh-price">{threshold ? formatPrice(w.max_price, w.currency) : "—"}</strong>
        <span className="ws-note">
          {!threshold ? "smart alerts only" : gap == null ? "\u00a0" : gap <= 0 ? "reached" : `${formatPrice(gap, w.currency)} to go`}
        </span>
      </div>
      <div className="wh-cell wh-score">
        <span className="muted small">Deal score</span>
        {s?.score != null && tone ? (
          <strong
            className={`score-pill score-${tone.tone}`}
            title={s.basis === "google" ? "How this price compares with Google's price history for the route" : "How this price compares with earlier checks of this watch"}
          >
            {s.score}
            <small>/100</small>
          </strong>
        ) : (
          <strong className="wh-price">—</strong>
        )}
        <span className="ws-note">{tone ? tone.label : "after 4 checks"}</span>
      </div>
    </div>
  );
}

/** Behind "Details": how it searches, what it costs, the price history and the alert rules. */
function WatchDetails({ watch: w, words, alerts, rules }: { watch: Watch; words: ReturnType<typeof describeWatch>; alerts: ReturnType<typeof alertSettingsOf>; rules: string[] }) {
  const { s, price, range, everLower, basis, legacy } = watchNumbers(w);
  return (
    <details className="watch-more watch-dim">
      <summary>
        Details <Icon name="chevronDown" size={14} />
      </summary>
      <div className="watch-more-body">
        <p className="watch-words small">
          {words.search} {w.active ? <strong>{alerts.threshold ? words.alert : "Smart alerts only."}</strong> : <strong>Paused — not checked daily until you resume it.</strong>}
        </p>
        {w.active && w.plan && planLine(w.plan) ? (
          <p className={`ws-plan small muted${planLow(w.plan) ? " ws-plan-low" : ""}`} title={planTip(w.plan)}>
            <Icon name="calendar" size={13} /> {planLine(w.plan)}
          </p>
        ) : (
          w.active &&
          w.credits &&
          w.credits.per_check > 0 && (
            <p className="ws-credits small muted" title="Live Google searches (SerpApi quota) one daily check may spend; the pre-screen often skips the confirmation">
              {w.credits.text}
            </p>
          )
        )}
        <div className="watch-stats">
          <div>
            <span className="muted small">30-day low</span>
            <strong>{price(s?.low_30d)}</strong>
            <span className="ws-note">{everLower ? `${price(s!.all_time_low)} all-time` : s ? "lowest so far" : "\u00a0"}</span>
          </div>
          <div>
            <span className="muted small">Typical</span>
            <strong title={range && s?.typical != null ? `Middle of Google's range: ${price(s.typical)}` : undefined}>{range ?? price(s?.typical)}</strong>
            <span className="ws-note">{range ? "per Google" : s ? `${s.basis_runs ?? s.runs} check${(s.basis_runs ?? s.runs) === 1 ? "" : "s"}` : "\u00a0"}</span>
          </div>
        </div>
        {basis && <p className="ws-basis small">{basis}</p>}
        {legacy &&
          (legacy.detail ? (
            <details className="ws-legacy small">
              <summary>{legacy.summary}</summary>
              <span>{legacy.detail}</span>
            </details>
          ) : (
            <p className="ws-legacy small">{legacy.summary}</p>
          ))}
        {rules.length > 0 && (
          <div className="watch-meta small" aria-label="Also alerts on">
            <span>Also alerts on</span>
            {rules.map((r) => (
              <span key={r} className="rule-chip">
                {r}
              </span>
            ))}
          </div>
        )}
      </div>
    </details>
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
  // Its dates are behind us: the server no longer checks it (backend engine.watch_credits).
  const passed = w.active && !!w.credits?.dates_passed;
  const hit = w.active && !passed && alerts.threshold && w.last_best_price != null && w.last_best_price <= w.max_price;
  // how far the last best price sits above the target, capped for the meter
  const ratio = w.last_best_price != null ? Math.min(1, w.max_price / w.last_best_price) : 0;
  const route = `${w.origin} to ${w.destination}`;
  const advice = w.stats && !legacyOnly(w.stats) ? w.stats.advice : null;
  return (
    <article className={`watch-card card${hit ? " watch-hit" : ""}${w.active && !passed ? "" : " watch-paused"}${editing ? " editing" : ""}`} id={`watch-${w.id}`}>
      <div className="watch-head">
        <div className="watch-dim watch-title">
          <h3 className="watch-route">
            {w.origin} → {w.destination}
          </h3>
          <p className="muted small">{[w.dates_hint, ...chips].join(" · ")}</p>
        </div>
        {passed ? (
          <span className="badge badge-expired" title="Its dates are in the past, so the daily run no longer checks it or spends searches on it">
            <Icon name="clock" size={11} /> Dates passed
          </span>
        ) : w.active ? (
          <span className={`badge ${hit ? "badge-good" : "badge-accent"}`}>{hit ? "Target reached" : "Active"}</span>
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
          {passed && (
            <p className="watch-words small watch-dim">
              <strong>Its dates have passed, so it is no longer checked. Give it new dates, or pause or delete it.</strong>
            </p>
          )}
          <WatchHeadline watch={w} hit={!!hit} threshold={alerts.threshold} />
          {w.last_best_price != null && alerts.threshold && (
            <div className="meter watch-dim" title={hit ? "Last run found a price under your target" : "How close the last best price is to your target"}>
              <span style={{ width: `${Math.round(ratio * 100)}%` }} />
            </div>
          )}
          {advice && showAdvice(advice, true) && (
            <div className="watch-dim watch-advice">
              <AdviceChip advice={advice} withReason />
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
          <WatchDetails watch={w} words={words} alerts={alerts} rules={rules} />
          <div className="watch-actions">
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => onRunNow(w.id)}
              disabled={busy || !w.active || passed}
              title={passed ? "Its dates have passed — edit them to run it" : w.active ? "Re-search this route now (same check the daily run does)" : "Resume the watch to run it"}
            >
              <Icon name="play" size={14} /> Run now
            </button>
            <button className="btn btn-ghost btn-sm" onClick={onEdit} aria-label={`Edit watch ${route}`}>
              <Icon name="edit" size={14} /> Edit
            </button>
            <AddToTrip compact item={{ kind: "watch", ref_id: w.id }} defaults={() => tripFromWatch(w)} />
            <button
              className="icon-btn push"
              onClick={onToggle}
              aria-label={`${w.active ? "Pause" : "Resume"} watch ${route}`}
              title={w.active ? "Pause: stop the daily check (and alerts) without losing the watch" : "Resume: include this watch in the daily check again"}
            >
              <Icon name={w.active ? "pause" : "play"} size={16} />
            </button>
            <button className="icon-btn icon-btn-danger" onClick={() => onDelete(w.id)} aria-label={`Delete watch ${route}`} title="Delete this watch">
              <Icon name="trash" size={16} />
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
  const [warning, setWarning] = useState<string | null>(null);
  const costKey = watches.map((w) => `${w.id}:${w.active}:${w.credits?.per_month ?? ""}`).join(",");

  useEffect(() => {
    let alive = true;
    fetchUsage()
      .then((r) => alive && setWarning(usageWarning(r)))
      .catch(() => {}); // best-effort banner
    return () => {
      alive = false;
    };
  }, [costKey]);

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
        <p className="muted small">
          Checked daily — you get an alert when a fare drops to your target.
          {paused > 0 && ` ${active} active · ${paused} paused.`}
        </p>
        <button className="btn btn-primary btn-sm" onClick={onCreate}>
          <Icon name="plus" size={15} /> New watch
        </button>
      </div>
      {warning && (
        <p className="usage-warning card small" role="status">
          <Icon name="alert" size={14} /> {warning}
        </p>
      )}
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
