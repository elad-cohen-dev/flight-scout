import { formatPrice, timeAgo } from "../lib/format";
import { criteriaChips } from "../lib/search";
import type { Watch } from "../types";
import Icon from "./Icon";

function WatchCard({
  watch: w,
  onRunNow,
  onDelete,
  busy,
}: {
  watch: Watch;
  onRunNow: (id: number) => void;
  onDelete: (id: number) => void;
  busy: boolean;
}) {
  const chips = criteriaChips(w);
  const hit = w.last_best_price != null && w.last_best_price <= w.max_price;
  // how far the last best price sits above the target, capped for the meter
  const ratio = w.last_best_price != null ? Math.min(1, w.max_price / w.last_best_price) : 0;
  return (
    <article className={`watch-card card${hit ? " watch-hit" : ""}`}>
      <div className="watch-head">
        <div>
          <h3 className="watch-route">
            {w.origin} → {w.destination}
          </h3>
          <p className="muted small">{w.dates_hint}</p>
        </div>
        <span className={`badge ${w.active ? "badge-good" : ""}`}>{w.active ? "Active" : "Paused"}</span>
      </div>
      {chips.length > 0 && (
        <div className="chip-row">
          {chips.map((c) => (
            <span key={c} className="badge">
              {c}
            </span>
          ))}
        </div>
      )}
      <div className="watch-stats">
        <div>
          <span className="muted small">Alert at</span>
          <strong>≤ {formatPrice(w.max_price, w.currency)}</strong>
        </div>
        <div>
          <span className="muted small">Last best</span>
          <strong className={hit ? "text-good" : ""}>
            {w.last_best_price != null ? formatPrice(w.last_best_price, w.currency) : "—"}
          </strong>
        </div>
        <div>
          <span className="muted small">Checked</span>
          <strong>{w.last_run_at ? timeAgo(w.last_run_at) : "Not yet"}</strong>
        </div>
      </div>
      {w.last_best_price != null && (
        <div className="meter" title={hit ? "Last run found a price under your target" : "How close the last best price is to your target"}>
          <span style={{ width: `${Math.round(ratio * 100)}%` }} />
        </div>
      )}
      <div className="watch-actions">
        <button className="btn btn-secondary btn-sm" onClick={() => onRunNow(w.id)} disabled={busy} title="Re-search this route now (same check the daily run does)">
          <Icon name="play" size={14} /> Run now
        </button>
        <button className="btn btn-ghost btn-sm btn-danger" onClick={() => onDelete(w.id)} aria-label={`Delete watch ${w.origin} to ${w.destination}`}>
          <Icon name="trash" size={14} /> Delete
        </button>
      </div>
    </article>
  );
}

export default function WatchesTab({
  watches,
  loading,
  onRunNow,
  onDelete,
  onCreate,
  busy,
}: {
  watches: Watch[];
  loading: boolean;
  onRunNow: (id: number) => void;
  onDelete: (id: number) => void;
  onCreate: () => void;
  busy: boolean;
}) {
  return (
    <div className="tab-watches">
      <div className="page-head">
        <div>
          <h2>Price watches</h2>
          <p className="muted small">Watched routes are re-checked daily; you get a Telegram alert when a deal drops to your target.</p>
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
          <span className="muted">Set up a search with a max price, then tap “Watch this route” — the agent will keep checking for you.</span>
          <button className="btn btn-primary" onClick={onCreate}>
            <Icon name="plus" size={16} /> Create a watch
          </button>
        </div>
      ) : (
        <div className="watch-grid">
          {watches.map((w) => (
            <WatchCard key={w.id} watch={w} onRunNow={onRunNow} onDelete={onDelete} busy={busy} />
          ))}
        </div>
      )}
    </div>
  );
}
