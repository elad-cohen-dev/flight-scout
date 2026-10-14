import type { AgentEvent } from "../api";
import { formatPrice, timeAgo } from "../lib/format";
import { insightsLine, priceLevelTone } from "../lib/providers";
import type { QueryGroup } from "../lib/history";
import type { SearchForm as Form } from "../lib/search";
import type { Job, SearchMeta, StoredDeal } from "../types";
import AgentConsole from "./AgentConsole";
import DealList from "./DealList";
import Icon from "./Icon";
import SearchForm from "./SearchForm";
import UsageMeter from "./UsageMeter";

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

export default function SearchTab({
  form,
  onFormChange,
  onSearch,
  onWatch,
  run,
  onStop,
  lastResult,
  recent,
  onPickRecent,
  onOpenHistory,
  priceRef,
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
  onPickRecent: (g: QueryGroup) => void;
  onOpenHistory: () => void;
  priceRef: React.RefObject<HTMLInputElement>;
  interrupted?: Job | null; // a run the server restart killed — offer to run it again
  onRunAgain?: () => void; // undefined when it can't be re-run from here
  onDismissInterrupted?: () => void;
  onCheckLive?: (deal: StoredDeal) => void; // an estimate's "Check this date live (1 search)"
}) {
  return (
    <div className="tab-search">
      <SearchForm form={form} onChange={onFormChange} onSearch={onSearch} onWatch={onWatch} busy={!!run} priceRef={priceRef} />

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

      {!run && lastResult && (
        <section className="results" aria-labelledby="results-title">
          <div className="section-head">
            <div>
              <h2 id="results-title">Results</h2>
              <p className="muted small">
                {lastResult.meta?.holiday && (
                  <span className="badge badge-holiday" title="Trips that fit this school vacation">
                    <Icon name="calendar" size={11} /> {lastResult.meta.holiday}
                  </span>
                )}{" "}
                {lastResult.query.replace(" -> ", " → ")} · {timeAgo(lastResult.finishedAt)}
              </p>
              {insightsLine(lastResult.meta?.price_insights) && (
                <p className={`insights-line insights-${priceLevelTone(lastResult.meta?.price_insights)}`} title="Google Flights' typical price range for the route on the dates named — one date pair — for your whole party">
                  {insightsLine(lastResult.meta?.price_insights)}
                </p>
              )}
              {!!lastResult.meta?.messages.length && (
                <ul className="search-notices">
                  {lastResult.meta.messages.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
          {lastResult.deals.length === 0 ? (
            <div className="empty card">
              <Icon name="search" size={28} />
              <strong>No deals found this time</strong>
              <span className="muted">Try flexible dates, allowing a stop, or a wider destination like a country.</span>
            </div>
          ) : (
            <DealList deals={lastResult.deals} onCheckLive={onCheckLive} advice={lastResult.meta?.advice} />
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
