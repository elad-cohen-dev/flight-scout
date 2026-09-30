import type { AgentEvent } from "../api";
import { formatPrice, timeAgo } from "../lib/format";
import type { QueryGroup } from "../lib/history";
import type { SearchForm as Form } from "../lib/search";
import type { StoredDeal } from "../types";
import AgentConsole from "./AgentConsole";
import DealList from "./DealList";
import Icon from "./Icon";
import SearchForm from "./SearchForm";

export interface RunState {
  title: string;
  events: AgentEvent[];
  stopping: boolean;
}

export interface LastResult {
  query: string;
  deals: StoredDeal[];
  finishedAt: string;
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
}) {
  return (
    <div className="tab-search">
      <SearchForm form={form} onChange={onFormChange} onSearch={onSearch} onWatch={onWatch} busy={!!run} priceRef={priceRef} />

      {run && <AgentConsole title={run.title} events={run.events} onStop={onStop} stopping={run.stopping} />}

      {!run && lastResult && (
        <section className="results" aria-labelledby="results-title">
          <div className="section-head">
            <div>
              <h2 id="results-title">Results</h2>
              <p className="muted small">
                {lastResult.query.replace(" -> ", " → ")} · {timeAgo(lastResult.finishedAt)}
              </p>
            </div>
          </div>
          {lastResult.deals.length === 0 ? (
            <div className="empty card">
              <Icon name="search" size={28} />
              <strong>No deals found this time</strong>
              <span className="muted">Try flexible dates, allowing a stop, or a wider destination like a country.</span>
            </div>
          ) : (
            <DealList deals={lastResult.deals} />
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
    </div>
  );
}
