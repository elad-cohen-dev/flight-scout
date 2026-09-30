import { useEffect, useMemo, useState } from "react";
import { formatPrice, plural, shortDate, timeAgo } from "../lib/format";
import { sectionize } from "../lib/history";
import type { QueryGroup } from "../lib/history";
import { watchForGroup } from "../lib/watches";
import type { WatchMatch } from "../lib/watches";
import type { Watch, WatchCreate } from "../types";
import DealList from "./DealList";
import Icon from "./Icon";
import PriceTrend from "./PriceTrend";
import Segmented from "./Segmented";
import Sparkline from "./Sparkline";
import WatchPopover from "./WatchPopover";

type Sort = "newest" | "cheapest" | "runs";
type GroupBy = "recent" | "route";
const PAGE = 12;
const PREFS_KEY = "fs_history_prefs";

function loadPrefs(): { sort: Sort; groupBy: GroupBy } {
  try {
    return { sort: "newest", groupBy: "recent", ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}") };
  } catch {
    return { sort: "newest", groupBy: "recent" };
  }
}

function QueryCard({
  group,
  match,
  directOnly,
  onRerun,
  onCreateWatch,
  onEditWatch,
  busy,
}: {
  group: QueryGroup;
  match: WatchMatch | null;
  directOnly: boolean;
  onRerun: (g: QueryGroup) => void;
  onCreateWatch: (w: WatchCreate) => Promise<boolean>;
  onEditWatch: (id: number) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);
  const [runIndex, setRunIndex] = useState(0);
  const { latest, previous } = group;
  const run = group.runs[Math.min(runIndex, group.runs.length - 1)];
  const deals = directOnly ? run.deals.filter((d) => d.stops === 0) : run.deals;
  const sameCurrency = previous && previous.currency === latest.currency;
  const bodyId = `qc-${group.key.replace(/[^a-z0-9]/gi, "").slice(0, 40)}-${group.runs.length}`;
  // the badge is for a watch running exactly this search; the bell edits any watch on the route
  const watched = match?.exact ? match.watch : null;
  const popId = `${bodyId}-watch`;

  return (
    <article className={`query-card card${open ? " open" : ""}`}>
      <div className="query-head">
        <button className="query-toggle" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls={bodyId}>
          <Icon name="chevronRight" size={16} className="query-chevron" />
          <span className="query-main">
            <span className="query-route">
              {group.route}
              {watched &&
                (watched.active ? (
                  <span className="badge badge-accent" title="A price watch re-checks this search daily">
                    <Icon name="bell" size={11} /> Watched
                  </span>
                ) : (
                  <span className="badge" title="The watch on this search is paused">
                    <Icon name="pause" size={11} /> Watch paused
                  </span>
                ))}
            </span>
            <span className="query-sub muted small">
              {group.datesHint}
              {group.chips.length > 0 && <> · {group.chips.join(" · ")}</>}
            </span>
          </span>
          <span className="query-stats">
            <span className="query-price">
              <span className="muted small">from </span>
              <strong>{formatPrice(latest.minPrice, latest.currency)}</strong>
            </span>
            <span className="query-meta small">
              <PriceTrend current={latest.minPrice} previous={sameCurrency ? previous.minPrice : null} currency={latest.currency} />
              <span className="muted">
                {group.runs.length > 1 ? `${group.runs.length} runs · ` : ""}
                {timeAgo(latest.foundAt)}
              </span>
            </span>
          </span>
          <span className="query-spark hide-xs" title={group.runs.length > 2 ? "Best price per run" : undefined}>
            {group.runs.length > 2 && <Sparkline values={[...group.runs].reverse().map((r) => r.minPrice)} />}
          </span>
        </button>
        <button
          className="icon-btn query-rerun"
          onClick={() => onRerun(group)}
          disabled={busy}
          title="Run this search again"
          aria-label={`Run ${group.route} again`}
        >
          <Icon name="refresh" size={17} />
        </button>
        {match ? (
          <button
            className="icon-btn query-watch watching"
            onClick={() => onEditWatch(match.watch.id)}
            title={match.exact ? "Edit watch" : "Edit the watch on this route"}
            aria-label={`Edit watch ${group.route}`}
          >
            <Icon name="bell" size={17} />
          </button>
        ) : (
          <button
            className="icon-btn query-watch"
            onClick={() => setWatchOpen(!watchOpen)}
            disabled={!group.request}
            aria-expanded={watchOpen}
            aria-controls={watchOpen ? popId : undefined}
            title="Watch this search — get an alert when the price drops"
            aria-label={`Watch ${group.route}`}
          >
            <Icon name="bell" size={17} />
          </button>
        )}
      </div>
      {watchOpen && !match && (
        <div id={popId}>
          <WatchPopover group={group} onCreate={onCreateWatch} onClose={() => setWatchOpen(false)} />
        </div>
      )}
      {open && (
        <div className="query-body" id={bodyId}>
          {group.runs.length > 1 && (
            <div className="run-picker" role="tablist" aria-label="Search runs">
              {group.runs.map((r, i) => (
                <button
                  key={r.key}
                  role="tab"
                  aria-selected={run === r}
                  className={`chip${run === r ? " chip-active" : ""}`}
                  onClick={() => setRunIndex(i)}
                >
                  {i === 0 ? "Latest" : shortDate(r.foundAt)} · {formatPrice(r.minPrice, r.currency)}
                </button>
              ))}
            </div>
          )}
          <p className="muted small run-caption">
            {plural(run.deals.length, "deal")} found {new Date(run.foundAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
            {group.runs.length > 1 && <> · best ever {formatPrice(group.bestEver, latest.currency)}</>}
          </p>
          {deals.length > 0 ? (
            <DealList deals={deals} />
          ) : (
            <p className="muted">No direct flights in this run.</p>
          )}
        </div>
      )}
    </article>
  );
}

export default function HistoryTab({
  groups,
  loading,
  watches,
  onRerun,
  onCreateWatch,
  onEditWatch,
  busy,
  onNewSearch,
}: {
  groups: QueryGroup[];
  loading: boolean;
  watches: Watch[];
  onRerun: (g: QueryGroup) => void;
  onCreateWatch: (w: WatchCreate) => Promise<boolean>;
  onEditWatch: (id: number) => void;
  busy: boolean;
  onNewSearch: () => void;
}) {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [prefs, setPrefs] = useState(loadPrefs);
  const [directOnly, setDirectOnly] = useState(false);
  const [withBags, setWithBags] = useState(false);
  const [watchedOnly, setWatchedOnly] = useState(false);
  const [limit, setLimit] = useState(PAGE);

  useEffect(() => {
    const t = setTimeout(() => setQuery(text.trim().toLowerCase()), 200);
    return () => clearTimeout(t);
  }, [text]);
  useEffect(() => {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {
      // best-effort
    }
  }, [prefs]);
  useEffect(() => setLimit(PAGE), [query, directOnly, withBags, watchedOnly, prefs]);

  const matches = useMemo(() => new Map(groups.map((g) => [g.key, watchForGroup(g, watches)])), [groups, watches]);
  const isWatched = (g: QueryGroup) => !!matches.get(g.key)?.exact;

  const filtered = useMemo(() => {
    const words = query.split(/\s+/).filter(Boolean);
    const list = groups.filter(
      (g) =>
        words.every((w) => g.haystack.includes(w)) &&
        (!directOnly || g.hasDirect) &&
        (!withBags || g.withBags) &&
        (!watchedOnly || isWatched(g))
    );
    const byNewest = (a: QueryGroup, b: QueryGroup) => b.latest.foundAt.localeCompare(a.latest.foundAt);
    if (prefs.sort === "newest") list.sort(byNewest);
    if (prefs.sort === "cheapest") list.sort((a, b) => a.latest.minPrice - b.latest.minPrice || byNewest(a, b));
    if (prefs.sort === "runs") list.sort((a, b) => b.runs.length - a.runs.length || byNewest(a, b));
    return list;
  }, [groups, query, directOnly, withBags, watchedOnly, prefs.sort, matches]);

  const sections = useMemo(() => sectionize(filtered.slice(0, limit), prefs.groupBy), [filtered, limit, prefs.groupBy]);
  const totalRuns = groups.reduce((n, g) => n + g.runs.length, 0);
  const anyWatched = groups.some(isWatched);
  const filtersOn = !!query || directOnly || withBags || watchedOnly;

  if (loading) {
    return (
      <div className="tab-history">
        <div className="skeleton skeleton-bar" />
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="skeleton skeleton-card" />
        ))}
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="empty card">
        <Icon name="history" size={32} />
        <strong>No searches yet</strong>
        <span className="muted">Every search you run lands here, grouped by route, so you can compare prices over time.</span>
        <button className="btn btn-primary" onClick={onNewSearch}>
          <Icon name="search" size={16} /> Start a search
        </button>
      </div>
    );
  }

  return (
    <div className="tab-history">
      <div className="page-head">
        <div>
          <h2>Search history</h2>
          <p className="muted small">
            {plural(groups.length, "search")} · {plural(totalRuns, "run")} — repeated runs of the same search are folded together.
          </p>
        </div>
      </div>

      <div className="toolbar card">
        <label className="search-box">
          <Icon name="search" size={16} />
          <input
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Filter by city, airport or airline"
            aria-label="Filter history"
          />
        </label>
        <div className="toolbar-row">
          <div className="chip-row">
            <button className={`chip chip-toggle${directOnly ? " chip-active" : ""}`} aria-pressed={directOnly} onClick={() => setDirectOnly(!directOnly)}>
              Direct flights
            </button>
            <button className={`chip chip-toggle${withBags ? " chip-active" : ""}`} aria-pressed={withBags} onClick={() => setWithBags(!withBags)}>
              <Icon name="luggage" size={13} /> Bags included
            </button>
            {anyWatched && (
              <button className={`chip chip-toggle${watchedOnly ? " chip-active" : ""}`} aria-pressed={watchedOnly} onClick={() => setWatchedOnly(!watchedOnly)}>
                <Icon name="bell" size={13} /> Watched
              </button>
            )}
          </div>
          <div className="toolbar-right">
            <label className="select-inline">
              <span className="muted small">Sort</span>
              <select value={prefs.sort} onChange={(e) => setPrefs({ ...prefs, sort: e.target.value as Sort })}>
                <option value="newest">Newest</option>
                <option value="cheapest">Cheapest</option>
                <option value="runs">Most runs</option>
              </select>
            </label>
            <Segmented
              label="Group by"
              size="sm"
              value={prefs.groupBy}
              onChange={(groupBy) => setPrefs({ ...prefs, groupBy })}
              options={[
                { value: "recent", label: "By date" },
                { value: "route", label: "By route" },
              ]}
            />
          </div>
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="empty card">
          <Icon name="filter" size={28} />
          <strong>Nothing matches</strong>
          <span className="muted">Try a different word or clear the filters.</span>
          {filtersOn && (
            <button
              className="btn btn-secondary"
              onClick={() => {
                setText("");
                setDirectOnly(false);
                setWithBags(false);
                setWatchedOnly(false);
              }}
            >
              Clear filters
            </button>
          )}
        </div>
      ) : (
        sections.map((s) => (
          <section key={s.title} className="history-section" aria-label={s.title}>
            <h3 className="section-title">
              {s.title}
              <span className="section-count">{s.groups.length}</span>
            </h3>
            <div className="query-list">
              {s.groups.map((g) => (
                <QueryCard
                  key={g.key}
                  group={g}
                  match={matches.get(g.key) ?? null}
                  directOnly={directOnly}
                  onRerun={onRerun}
                  onCreateWatch={onCreateWatch}
                  onEditWatch={onEditWatch}
                  busy={busy}
                />
              ))}
            </div>
          </section>
        ))
      )}

      {filtered.length > limit && (
        <div className="show-more">
          <button className="btn btn-secondary" onClick={() => setLimit(limit + PAGE)}>
            Show {Math.min(PAGE, filtered.length - limit)} more
          </button>
          <span className="muted small">
            Showing {limit} of {filtered.length}
          </span>
        </div>
      )}
    </div>
  );
}
