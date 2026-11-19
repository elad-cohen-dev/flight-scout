import { useEffect, useMemo, useState } from "react";
import type { AgentEvent } from "../api";
import { showAdvice } from "../lib/advice";
import { formatPrice, timeAgo } from "../lib/format";
import { insightsLine, priceLevelTone } from "../lib/providers";
import type { QueryGroup } from "../lib/history";
import type { SearchForm as Form } from "../lib/search";
import { watchForGroup } from "../lib/watches";
import type { Job, SearchMeta, StoredDeal, Watch, WatchCreate } from "../types";
import AdviceChip from "./AdviceChip";
import AgentConsole from "./AgentConsole";
import DealList from "./DealList";
import Icon from "./Icon";
import SearchForm from "./SearchForm";
import UsageMeter from "./UsageMeter";
import WatchPopover from "./WatchPopover";

export interface RunState {
  title: string;
  events: AgentEvent[];
  stopping: boolean;
}

export interface LastResult {
  query: string;
  deals: StoredDeal[];
  finishedAt: string;
  meta?: SearchMeta | null; // sources, Google price insights, notices (searches only)
}

/** "TLV -> Prague (10–20 Dec, direct only)" → the route and what's in the parens. */
function splitQuery(query: string): { route: string; criteria: string } {
  const text = query.replace(/\s*\[watch #\d+\]\s*$/, "");
  const at = text.indexOf(" (");
  const route = (at < 0 ? text : text.slice(0, at)).replace(" -> ", " → ");
  return { route, criteria: at < 0 ? "" : text.slice(at + 2).replace(/\)$/, "") };
}

export default function SearchTab({
  form,
  onFormChange,
  onSearch,
  onWatch,
  run,
  onStop,
  lastResult,
  recent,
  groups,
  watches,
  onPickRecent,
  onOpenHistory,
  onCreateWatch,
  onEditWatch,
  priceNudge,
  interrupted = null,
  onRunAgain,
  onDismissInterrupted,
  onCheckLive,
}: {
  form: Form;
  onFormChange: (patch: Partial<Form>) => void;
  onSearch: () => void;
  onWatch: () => void;
  run: RunState | null;
  onStop: () => void;
  lastResult: LastResult | null;
  recent: QueryGroup[];
  groups: QueryGroup[]; // every stored search: the one behind the results gives them a tidy title and a watch
  watches: Watch[];
  onPickRecent: (g: QueryGroup) => void;
  onOpenHistory: () => void;
  onCreateWatch: (w: WatchCreate) => Promise<boolean>;
  onEditWatch: (id: number) => void;
  priceNudge: number;
  interrupted?: Job | null; // a run the server restart killed — offer to run it again
  onRunAgain?: () => void; // undefined when it can't be re-run from here
  onDismissInterrupted?: () => void;
  onCheckLive?: (deal: StoredDeal) => void; // an estimate's "Check this date live (1 search)"
}) {
  const [watchOpen, setWatchOpen] = useState(false);
  useEffect(() => setWatchOpen(false), [lastResult?.finishedAt]);

  // The stored search these results belong to: its criteria, parsed and ready.
  const group = useMemo(() => {
    if (!lastResult?.deals.length) return null;
    const ids = new Set(lastResult.deals.map((d) => d.id));
    return groups.find((g) => g.runs.some((r) => r.deals.some((d) => ids.has(d.id)))) ?? null;
  }, [lastResult, groups]);
  const match = useMemo(() => (group ? watchForGroup(group, watches) : null), [group, watches]);

  const idle = !run && !lastResult && !interrupted;
  const meta = lastResult?.meta;
  const parsed = lastResult ? splitQuery(lastResult.query) : null;
  const insights = insightsLine(meta?.price_insights);
  const advice = showAdvice(meta?.advice) ? meta.advice : null;

  return (
    <div className="tab-search">
      {idle && (
        <header className="hero">
          <h1>Where to next?</h1>
          <p className="muted">Live fares with your bags in the price — and a watch on any route you like.</p>
        </header>
      )}

      <SearchForm form={form} onChange={onFormChange} onSearch={onSearch} onWatch={onWatch} busy={!!run} priceNudge={priceNudge} />

      {run && <AgentConsole title={run.title} events={run.events} onStop={onStop} stopping={run.stopping} />}

      {!run && interrupted && (
        <section className="interrupted card" role="alert">
          <div className="interrupted-head">
            <Icon name="alert" size={18} />
            <strong>{interrupted.kind === "search" ? "Search interrupted" : "Watch run interrupted"}</strong>
          </div>
          <p className="muted small">{interrupted.query.replace(" -> ", " → ")}</p>
          <p>{interrupted.error ?? "The server restarted while this was running."}</p>
          <div className="interrupted-actions">
            {onRunAgain && (
              <button className="btn btn-primary btn-sm" onClick={onRunAgain}>
                <Icon name="refresh" size={14} /> Run again
              </button>
            )}
            <button className="btn btn-ghost btn-sm" onClick={onDismissInterrupted}>
              Dismiss
            </button>
          </div>
          {onRunAgain && interrupted.kind === "search" && (
            <p className="muted small">Running again starts a fresh search, which may use a live fare-API credit.</p>
          )}
        </section>
      )}

      {!run && lastResult && parsed && (
        <section className="results" aria-labelledby="results-title">
          <div className="results-head">
            <div className="results-title">
              <h2 id="results-title">{group?.route ?? parsed.route}</h2>
              <p className="muted small">
                {meta?.holiday && (
                  <span className="badge badge-holiday" title="Trips that fit this school vacation">
                    <Icon name="calendar" size={11} /> {meta.holiday}
                  </span>
                )}{" "}
                {group ? [group.datesHint, ...group.chips].filter(Boolean).join(" · ") : parsed.criteria}
                {" · "}
                {timeAgo(lastResult.finishedAt)}
              </p>
            </div>
            {group &&
              (match ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm results-track tracking"
                  onClick={() => onEditWatch(match.watch.id)}
                  title={match.exact ? "A watch re-checks this search — open it" : "There is a watch on this route — open it"}
                >
                  <Icon name="bell" size={15} /> Tracking · {formatPrice(match.watch.max_price, match.watch.currency)}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm results-track"
                  onClick={() => setWatchOpen(!watchOpen)}
                  disabled={!group.request}
                  aria-expanded={watchOpen}
                  title="Re-check this search daily and get an alert when the price drops"
                >
                  <Icon name="bell" size={15} /> Track prices
                </button>
              ))}
          </div>

          {watchOpen && group && !match && (
            <div className="results-watch card">
              <WatchPopover group={group} onCreate={onCreateWatch} onClose={() => setWatchOpen(false)} />
            </div>
          )}

          {(insights || advice) && (
            <div
              className={`insight insight-${advice ? "advice" : priceLevelTone(meta?.price_insights)}`}
              title="Google Flights' typical price range for the route on the dates named — one date pair — for your whole party"
            >
              {advice ? <AdviceChip advice={advice} /> : <Icon name="chart" size={16} />}
              <div className="insight-text">
                {advice && <strong>{advice.reason}</strong>}
                {insights && <span>{insights}</span>}
              </div>
            </div>
          )}
          {!!meta?.messages.length && (
            <ul className="search-notices">
              {meta.messages.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          )}

          {meta?.dates_passed ? (
            <div className="empty card">
              <Icon name="calendar" size={28} />
              <strong>Those dates have already passed</strong>
              <span className="muted">Nothing was searched, so no live searches were spent. Pick dates from today on and search again.</span>
            </div>
          ) : lastResult.deals.length === 0 ? (
            <div className="empty card">
              <Icon name="search" size={28} />
              <strong>No deals found this time</strong>
              <span className="muted">Try flexible dates, allowing a stop, or a wider destination like a country.</span>
            </div>
          ) : (
            <DealList deals={lastResult.deals} onCheckLive={onCheckLive} />
          )}
        </section>
      )}

      {!run && !lastResult && recent.length > 0 && (
        <section className="recent" aria-labelledby="recent-title">
          <div className="section-head">
            <h2 id="recent-title">Recent searches</h2>
            <button className="btn btn-link" onClick={onOpenHistory}>
              All history <Icon name="chevronRight" size={14} />
            </button>
          </div>
          <div className="recent-grid">
            {recent.map((g) => (
              <button key={g.key} className="recent-card card" onClick={() => onPickRecent(g)} title="Fill the form with this search">
                <span className="recent-route">{g.route}</span>
                <span className="muted small recent-hint">{g.datesHint}</span>
                <span className="recent-price">
                  from <strong>{formatPrice(g.latest.minPrice, g.latest.currency)}</strong>
                  <span className="muted small"> · {timeAgo(g.latest.foundAt)}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <UsageMeter refreshKey={lastResult?.finishedAt} />
    </div>
  );
}
