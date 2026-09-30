import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AuthError,
  cancelJob,
  clearAppPassword,
  createWatch,
  deleteWatch,
  fetchDeals,
  fetchHistory,
  fetchWatches,
  followJob,
  setAppPassword,
  startSearch,
  startWatchRun,
  updateWatch,
} from "./api";
import type { AgentEvent } from "./api";
import HistoryTab from "./components/HistoryTab";
import Icon from "./components/Icon";
import type { IconName } from "./components/Icon";
import LockScreen from "./components/LockScreen";
import SearchTab from "./components/SearchTab";
import type { LastResult, RunState } from "./components/SearchTab";
import { ToastProvider, useToast } from "./components/Toasts";
import TrendsTab from "./components/TrendsTab";
import WatchesTab from "./components/WatchesTab";
import { formatPrice } from "./lib/format";
import { groupHistory } from "./lib/history";
import type { QueryGroup } from "./lib/history";
import { formToRequest, formToWatch, loadForm, requestToForm, rerunRequest, saveForm } from "./lib/search";
import type { SearchForm } from "./lib/search";
import { useHashTab } from "./lib/useHashTab";
import type { Tab } from "./lib/useHashTab";
import type { RouteHistory, SearchRequest, StoredDeal, Watch, WatchCreate, WatchUpdate } from "./types";

const NAV: { tab: Tab; label: string; icon: IconName }[] = [
  { tab: "search", label: "Search", icon: "search" },
  { tab: "history", label: "History", icon: "history" },
  { tab: "watches", label: "Watches", icon: "bell" },
  { tab: "trends", label: "Trends", icon: "chart" },
];

function FlightScout() {
  const toast = useToast();
  const [tab, go] = useHashTab();
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [deals, setDeals] = useState<StoredDeal[]>([]);
  const [watches, setWatches] = useState<Watch[]>([]);
  const [history, setHistory] = useState<RouteHistory[]>([]);
  const [form, setForm] = useState<SearchForm>(loadForm);
  const [run, setRun] = useState<RunState | null>(null);
  const [lastResult, setLastResult] = useState<LastResult | null>(null);
  const currentJobId = useRef<string | null>(null);
  const priceRef = useRef<HTMLInputElement>(null);

  const lock = useCallback(() => {
    clearAppPassword();
    setLocked(true);
  }, []);

  const loadAll = useCallback(async () => {
    try {
      // deals decide auth/errors; watches and history are best-effort extras
      const [d, w, h] = await Promise.all([
        fetchDeals(),
        fetchWatches().catch(() => [] as Watch[]),
        fetchHistory().catch(() => [] as RouteHistory[]),
      ]);
      setDeals(d);
      setWatches(w);
      setHistory(h);
      setLocked(false);
    } catch (e) {
      if (e instanceof AuthError) lock();
      else toast("error", `Couldn't load your data — ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [lock, toast]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => saveForm(form), [form]);

  const groups = useMemo(() => groupHistory(deals), [deals]);
  const recent = useMemo(
    () => [...groups].sort((a, b) => b.latest.foundAt.localeCompare(a.latest.foundAt)).slice(0, 4),
    [groups]
  );

  async function unlock(password: string): Promise<string | null> {
    setAppPassword(password);
    try {
      await fetchDeals();
    } catch (e) {
      if (e instanceof AuthError) {
        clearAppPassword();
        return "Wrong password, try again.";
      }
      return String(e);
    }
    setLoading(true);
    await loadAll();
    return null;
  }

  function handleRunError(e: unknown) {
    if (e instanceof AuthError) lock();
    else toast("error", String(e));
  }

  const onAgentEvent = (e: AgentEvent) => {
    if (e.kind !== "end") setRun((r) => (r ? { ...r, events: [...r.events, e] } : r));
  };

  function onStop() {
    const jobId = currentJobId.current;
    if (!jobId) return;
    setRun((r) => (r ? { ...r, stopping: true } : r));
    cancelJob(jobId).catch(() => setRun((r) => (r ? { ...r, stopping: false } : r)));
  }

  async function performSearch(req: SearchRequest) {
    if (run) return;
    setRun({ title: `Searching ${req.origin} → ${req.destination}`, events: [], stopping: false });
    setLastResult(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
    try {
      const started = await startSearch(req);
      currentJobId.current = started.job_id;
      const job = await followJob(started, onAgentEvent);
      if (job.status === "error") toast("error", job.error ?? "Search failed");
      else if (job.status === "cancelled") toast("info", "Search stopped — nothing was saved.");
      else {
        const found = job.deals ?? [];
        setDeals((prev) => [...found, ...prev]);
        setLastResult({ query: job.query, deals: found, finishedAt: new Date().toISOString() });
        fetchHistory().then(setHistory).catch(() => {});
        toast("success", found.length ? `Found ${found.length} deal${found.length > 1 ? "s" : ""}.` : "Search finished — no deals this time.");
      }
    } catch (e) {
      handleRunError(e);
    } finally {
      setRun(null);
      currentJobId.current = null;
    }
  }

  function onSearch() {
    const req = formToRequest(form);
    if (!req.origin || !req.destination) {
      toast("error", "Fill in where you're flying from and to.");
      return;
    }
    performSearch(req);
  }

  function onRerun(group: QueryGroup) {
    const req = rerunRequest(group.latest.deals[0]);
    if (!req) {
      toast("error", "Couldn't recover this search's criteria.");
      return;
    }
    setForm((f) => requestToForm(req, f)); // reflect the re-run in the form
    go("search");
    performSearch(req);
  }

  function onPickRecent(group: QueryGroup) {
    const req = rerunRequest(group.latest.deals[0]);
    if (!req) return;
    setForm((f) => requestToForm(req, f));
    window.scrollTo({ top: 0, behavior: "smooth" });
    toast("info", "Criteria loaded — adjust anything, then Search.");
  }

  async function onRunWatch(id: number) {
    if (run) return;
    const w = watches.find((x) => x.id === id);
    setRun({ title: `Checking watch ${w ? `${w.origin} → ${w.destination}` : ""}`, events: [], stopping: false });
    setLastResult(null);
    go("search");
    try {
      const started = await startWatchRun(id);
      currentJobId.current = started.job_id;
      const job = await followJob(started, onAgentEvent);
      if (job.status === "error") toast("error", job.error ?? "Watch run failed");
      else if (job.status === "cancelled") toast("info", "Watch run stopped — nothing was saved.");
      else {
        loadAll(); // fresh deals, watch stats (last best / checked), history point
        if (job.deals) setLastResult({ query: job.query, deals: job.deals, finishedAt: new Date().toISOString() });
        const r = ((job.summary?.results as Record<string, unknown>[] | undefined) ?? [])[0];
        if (r?.error) toast("error", String(r.error));
        else if (r) {
          const currency = String(r.currency ?? "USD");
          toast(
            r.alerted ? "success" : "info",
            r.best_price == null
              ? `Checked ${r.route}: no comparable deals this run.`
              : `Checked ${r.route}: best ${formatPrice(Number(r.best_price), currency)} vs your ${formatPrice(Number(r.threshold), currency)} target — ${r.alerted ? "alert sent!" : "no alert."}`
          );
        }
      }
    } catch (e) {
      handleRunError(e);
    } finally {
      setRun(null);
      currentJobId.current = null;
    }
  }

  async function onWatch() {
    const watch = formToWatch(form);
    if (!form.origin.trim() || !form.destination.trim()) {
      toast("error", "Fill in where you're flying from and to first.");
      return;
    }
    if (!watch) {
      toast("error", "Set a max total price — you'll be alerted when a deal is at or below it.");
      priceRef.current?.focus();
      return;
    }
    try {
      const created = await createWatch(watch);
      setWatches((prev) => [...prev, created]);
      toast("success", `Watching ${created.origin} → ${created.destination} for deals ≤ ${formatPrice(created.max_price, created.currency)}.`);
    } catch (e) {
      handleRunError(e);
    }
  }

  /** Create a watch from a History card; true on success. */
  async function onCreateWatchFrom(watch: WatchCreate): Promise<boolean> {
    try {
      const created = await createWatch(watch);
      setWatches((prev) => [...prev, created]);
      toast("success", `Watching ${created.origin} → ${created.destination} — you'll be alerted at ≤ ${formatPrice(created.max_price, created.currency)}.`);
      return true;
    } catch (e) {
      handleRunError(e);
      return false;
    }
  }

  /** Edit or pause/resume a watch; true on success. */
  async function onUpdateWatch(id: number, patch: WatchUpdate): Promise<boolean> {
    try {
      const updated = await updateWatch(id, patch);
      setWatches((prev) => prev.map((w) => (w.id === id ? updated : w)));
      const route = `${updated.origin} → ${updated.destination}`;
      if (Object.keys(patch).length === 1 && "active" in patch) {
        toast("info", updated.active ? `Resumed the watch on ${route}.` : `Paused the watch on ${route} — the daily run will skip it.`);
      } else toast("success", `Saved the watch on ${route}.`);
      return true;
    } catch (e) {
      handleRunError(e);
      return false;
    }
  }

  function onEditWatch(id: number) {
    window.location.hash = `watches/${id}`; // the Watches tab opens that watch's editor
    window.scrollTo({ top: 0 });
  }

  async function onDeleteWatch(id: number) {
    const watch = watches.find((w) => w.id === id);
    const label = watch ? `${watch.origin} → ${watch.destination}` : "this route";
    if (!window.confirm(`Delete the watch on ${label}? Daily checks and alerts for it will stop.`)) return;
    try {
      await deleteWatch(id);
      setWatches((prev) => prev.filter((w) => w.id !== id));
      toast("info", `Deleted the watch on ${label}.`);
    } catch (e) {
      handleRunError(e);
    }
  }

  function onCreateWatch() {
    go("search");
    setTimeout(() => priceRef.current?.focus(), 50);
    toast("info", "Fill in the route and a max price, then tap “Watch this route”.");
  }

  if (locked) return <LockScreen onUnlock={unlock} />;

  const counts: Partial<Record<Tab, number>> = { history: groups.length, watches: watches.length };

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <a className="brand" href="#search" aria-label="Flight Scout home">
            <span className="brand-mark" aria-hidden="true">
              <Icon name="plane" size={18} />
            </span>
            <span className="brand-text">
              <strong>Flight Scout</strong>
              <span className="muted small hide-xs">An agent that hunts the web for cheap flights</span>
            </span>
          </a>
          <nav className="tabs" aria-label="Sections">
            {NAV.map((n) => (
              <a
                key={n.tab}
                href={`#${n.tab}`}
                className={`tab${tab === n.tab ? " active" : ""}`}
                aria-current={tab === n.tab ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(n.tab);
                }}
              >
                <span className="tab-icon">
                  <Icon name={n.icon} size={20} />
                  {n.tab === "search" && run && <span className="tab-live" aria-label="search running" />}
                </span>
                <span className="tab-label">{n.label}</span>
                {!!counts[n.tab] && <span className="tab-count">{counts[n.tab]}</span>}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <main className="container">
        {run && tab !== "search" && (
          <button className="live-banner" onClick={() => go("search")}>
            <span className="agent-dot" aria-hidden="true" /> {run.title}… <span className="live-banner-cta">View live</span>
          </button>
        )}
        {tab === "search" && (
          <SearchTab
            form={form}
            onFormChange={(patch) => setForm((f) => ({ ...f, ...patch }))}
            onSearch={onSearch}
            onWatch={onWatch}
            run={run}
            onStop={onStop}
            lastResult={lastResult}
            recent={recent}
            onPickRecent={onPickRecent}
            onOpenHistory={() => go("history")}
            priceRef={priceRef}
          />
        )}
        {tab === "history" && (
          <HistoryTab
            groups={groups}
            loading={loading}
            watches={watches}
            onRerun={onRerun}
            onCreateWatch={onCreateWatchFrom}
            onEditWatch={onEditWatch}
            busy={!!run}
            onNewSearch={() => go("search")}
          />
        )}
        {tab === "watches" && (
          <WatchesTab
            watches={watches}
            loading={loading}
            onRunNow={onRunWatch}
            onDelete={onDeleteWatch}
            onUpdate={onUpdateWatch}
            onCreate={onCreateWatch}
            busy={!!run}
          />
        )}
        {tab === "trends" && (
          <TrendsTab
            history={history}
            loading={loading}
            deals={deals}
            onSearchDates={(req, note) => {
              // Pre-fill only: each search costs money, so the user presses Search.
              setForm((f) => requestToForm(req, f));
              go("search");
              window.scrollTo({ top: 0, behavior: "smooth" });
              toast("info", note);
            }}
          />
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <FlightScout />
    </ToastProvider>
  );
}
