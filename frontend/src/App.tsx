import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  getJob,
  HttpError,
  pingHealth,
  setAppPassword,
  startSearch,
  startWatchRun,
  updateWatch,
} from "./api";
import type { AgentEvent } from "./api";
import GuideTab from "./components/GuideTab";
import HistoryTab from "./components/HistoryTab";
import Icon from "./components/Icon";
import type { IconName } from "./components/Icon";
import LockScreen from "./components/LockScreen";
import SearchTab from "./components/SearchTab";
import type { LastResult, RunState } from "./components/SearchTab";
import { ToastProvider, useToast } from "./components/Toasts";
import TrendsTab from "./components/TrendsTab";
import { TripsProvider } from "./components/TripsContext";
import TripsTab from "./components/TripsTab";
import WakeBanner from "./components/WakeBanner";
import WatchesTab from "./components/WatchesTab";
// Explore (with Leaflet) loads on first visit, outside the main bundle.
const ExploreTab = lazy(() => import("./components/ExploreTab"));
import { formatPrice } from "./lib/format";
import { fetchGuides } from "./guideApi";
import { clearCachedGuides, loadCachedGuides, pickGuide, readGuides, saveCachedGuides, todayISO } from "./lib/guide";
import type { TripGuide } from "./lib/guide";
import { groupHistory } from "./lib/history";
import type { QueryGroup } from "./lib/history";
import { forgetJob, loadActiveJob, rememberJob, rerunTarget, waitForServer } from "./lib/jobs";
import { checkLiveRequest, formToRequest, formToWatch, loadForm, requestToForm, rerunRequest, saveForm } from "./lib/search";
import type { SearchForm } from "./lib/search";
import { useHashTab } from "./lib/useHashTab";
import type { Tab } from "./lib/useHashTab";
import { alertSettingsOf } from "./lib/watches";
import type { Job, RouteHistory, SearchRequest, StoredDeal, Watch, WatchCreate, WatchUpdate } from "./types";

/** A remembered job that finished longer ago than this isn't resurfaced on load. */
const RESUME_RESULT_MAX_AGE_MS = 6 * 60 * 60_000;

/** The header has five sections. "Tracking" is everything that follows a price
 *  over time: its three pages keep their own hashes (#watches, #history,
 *  #trends), so links, reloads and the back button work as before. */
type Section = "search" | "explore" | "tracking" | "trips" | "guide";
const TRACKING: { tab: Tab; label: string; icon: IconName }[] = [
  { tab: "watches", label: "Watches", icon: "bell" },
  { tab: "history", label: "History", icon: "history" },
  { tab: "trends", label: "Trends", icon: "chart" },
];
const isTracking = (t: Tab) => TRACKING.some((x) => x.tab === t);
const sectionOf = (t: Tab): Section => (isTracking(t) ? "tracking" : (t as Section));

const NAV: { section: Section; label: string; icon: IconName }[] = [
  { section: "search", label: "Search", icon: "search" },
  { section: "explore", label: "Explore", icon: "map" },
  { section: "tracking", label: "Tracking", icon: "bell" },
  { section: "trips", label: "Trips", icon: "suitcase" },
  { section: "guide", label: "Guide", icon: "pin" }, // shown under the trip's own name once a guide is loaded
];

const TRACKING_KEY = "fs_tracking_tab";
function lastTrackingTab(): Tab | null {
  try {
    const t = localStorage.getItem(TRACKING_KEY) as Tab | null;
    return t && isTracking(t) ? t : null;
  } catch {
    return null;
  }
}

function FlightScout() {
  const toast = useToast();
  const [tab, go] = useHashTab();
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [deals, setDeals] = useState<StoredDeal[]>([]);
  const [watches, setWatches] = useState<Watch[]>([]);
  const [history, setHistory] = useState<RouteHistory[]>([]);
  // The trip guide opens from its on-device copy (offline, or while the server wakes).
  const [guides, setGuides] = useState<TripGuide[]>(loadCachedGuides);
  const [guidesLoaded, setGuidesLoaded] = useState(false);
  const [form, setForm] = useState<SearchForm>(loadForm);
  const [run, setRun] = useState<RunState | null>(null);
  const [lastResult, setLastResult] = useState<LastResult | null>(null);
  const [interrupted, setInterrupted] = useState<Job | null>(null);
  const [wakingSince, setWakingSince] = useState<number | null>(null);
  const currentJobId = useRef<string | null>(null);
  const [priceNudge, setPriceNudge] = useState(0); // bumped to open the search bar's price filter

  const lock = useCallback(() => {
    clearAppPassword();
    clearCachedGuides(); // private data: it goes with the password
    setGuides([]);
    setLocked(true);
  }, []);

  const applyGuides = useCallback((stored: Awaited<ReturnType<typeof fetchGuides>>) => {
    const list = readGuides(stored);
    setGuides(list);
    saveCachedGuides(list);
    setGuidesLoaded(true);
  }, []);

  const loadAll = useCallback(async () => {
    try {
      // deals decide auth/errors; watches, history and the guide are best-effort extras
      const [d, w, h, g] = await Promise.all([
        fetchDeals(),
        fetchWatches().catch(() => [] as Watch[]),
        fetchHistory().catch(() => [] as RouteHistory[]),
        fetchGuides().catch(() => null), // on failure the on-device copy stays
      ]);
      setDeals(d);
      setWatches(w);
      setHistory(h);
      if (g) applyGuides(g);
      setLocked(false);
      return true;
    } catch (e) {
      if (e instanceof AuthError) lock();
      else toast("error", `Couldn't load your data — ${String(e)}`);
      return false;
    } finally {
      setLoading(false);
      setGuidesLoaded(true); // answered or not: the Guide tab stops waiting and shows what it has
    }
  }, [lock, toast, applyGuides]);

  /** Resolve once the server answers, showing the wake-up banner if it's
   *  asleep (free hosting) — before the first load and before starting runs. */
  const ensureAwake = useCallback(async () => {
    await waitForServer({ ping: () => pingHealth(), onSlow: () => setWakingSince(Date.now()) });
    setWakingSince(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    ensureAwake().then(async () => {
      if (cancelled) return;
      if (await loadAll()) resumeActiveJob();
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per page load
  }, []);

  useEffect(() => saveForm(form), [form]);
  // "Tracking" reopens on the page last looked at.
  useEffect(() => {
    if (!isTracking(tab)) return;
    try {
      localStorage.setItem(TRACKING_KEY, tab);
    } catch {
      // best-effort
    }
  }, [tab]);

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
    if (await loadAll()) resumeActiveJob();
    return null;
  }

  function handleRunError(e: unknown) {
    if (e instanceof AuthError) lock();
    else toast("error", String(e));
  }
  // The same, but stable: the Trips tab and pickers refetch when their handlers change.
  const onTripsError = useCallback(
    (e: unknown) => {
      if (e instanceof AuthError) lock();
      else toast("error", String(e));
    },
    [lock, toast]
  );

  const onAgentEvent = (e: AgentEvent) => {
    if (e.kind !== "end") setRun((r) => (r ? { ...r, events: [...r.events, e] } : r));
  };

  function onStop() {
    const jobId = currentJobId.current;
    if (!jobId) return;
    setRun((r) => (r ? { ...r, stopping: true } : r));
    cancelJob(jobId).catch(() => setRun((r) => (r ? { ...r, stopping: false } : r)));
  }

  /** Follow a started (or re-attached) job to its end, remembering it so a
   *  page reload picks it up again instead of losing it. */
  async function track(job: Job, title: string) {
    rememberJob({ job_id: job.job_id, kind: job.kind, title });
    currentJobId.current = job.job_id;
    setRun({ title, events: [], stopping: false });
    try {
      finishJob(await followJob(job, onAgentEvent));
      forgetJob(job.job_id);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) {
        forgetJob(job.job_id);
        toast("error", "The server no longer knows this run — start it again if you still need it.");
      } else handleRunError(e); // remembered: a reload re-attaches once the server is back
    } finally {
      setRun(null);
      currentJobId.current = null;
    }
  }

  /** Show a finished job's outcome. */
  function finishJob(job: Job) {
    const what = job.kind === "search" ? "Search" : "Watch run";
    if (job.status === "error") toast("error", job.error ?? `${what} failed`);
    else if (job.status === "cancelled") toast("info", `${what} stopped — nothing was saved.`);
    else if (job.status === "interrupted") {
      setInterrupted(job);
      toast("error", `${what} interrupted by a server restart.`);
    } else if (job.kind === "search") {
      const found = job.deals ?? [];
      const ids = new Set(found.map((d) => d.id));
      // after a reload these deals may already be in the fetched list
      setDeals((prev) => [...found, ...prev.filter((d) => !ids.has(d.id))]);
      setLastResult({ query: job.query, deals: found, finishedAt: job.updated_at ?? new Date().toISOString(), meta: job.meta });
      fetchHistory().then(setHistory).catch(() => {});
      if (job.meta?.dates_passed) toast("info", "Those dates have already passed — nothing was searched.");
      else toast("success", found.length ? `Found ${found.length} deal${found.length > 1 ? "s" : ""}.` : "Search finished — no deals this time.");
    } else {
      loadAll(); // fresh deals, watch stats (last best / checked), history point
      if (job.deals) setLastResult({ query: job.query, deals: job.deals, finishedAt: new Date().toISOString() });
      const r = ((job.summary?.results as Record<string, unknown>[] | undefined) ?? [])[0];
      if (r?.error) toast("error", String(r.error));
      else if (r?.expired) toast("info", `${r.route}: its dates have passed, so nothing was searched. Give the watch new dates, or pause or delete it.`);
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
  }

  /** After a reload: pick the remembered job back up (live, or its result). */
  async function resumeActiveJob() {
    const saved = loadActiveJob();
    if (!saved || currentJobId.current) return;
    let job: Job;
    try {
      job = await getJob(saved.job_id);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) forgetJob(saved.job_id);
      return;
    }
    if (job.status === "running") {
      if (saved.kind === "search") go("search");
      await track(job, saved.title);
    } else {
      forgetJob(job.job_id);
      // Finished while the page was closed: show it, unless that was long ago.
      if (Date.now() - Date.parse(job.updated_at ?? "") < RESUME_RESULT_MAX_AGE_MS) finishJob(job);
    }
  }

  /** Start a job: shows the console at once, wakes a sleeping server first. */
  async function launch(title: string, start: () => Promise<Job>) {
    if (run) return;
    setRun({ title, events: [], stopping: false });
    setLastResult(null);
    setInterrupted(null);
    let started: Job;
    try {
      await ensureAwake();
      started = await start();
    } catch (e) {
      handleRunError(e);
      setRun(null);
      return;
    }
    await track(started, title);
  }

  async function performSearch(req: SearchRequest) {
    window.scrollTo({ top: 0, behavior: "smooth" });
    await launch(`Searching ${req.origin} → ${req.destination}`, () => startSearch(req));
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

  /** An estimate's "Check this date live": the same search, for its exact date pair. */
  function onCheckLive(deal: StoredDeal) {
    const req = checkLiveRequest(deal);
    if (!req) {
      toast("error", "Couldn't recover this fare's dates to check them live.");
      return;
    }
    setForm((f) => requestToForm(req, f));
    performSearch(req);
  }

  /** A trip's "Re-run" on a linked search: the search behind its latest run's deal. */
  function onRerunDeal(deal: StoredDeal) {
    const req = rerunRequest(deal);
    if (!req) {
      toast("error", "Couldn't recover this search's criteria.");
      return;
    }
    setForm((f) => requestToForm(req, f));
    go("search");
    performSearch(req);
  }

  /** A trip's "Open" on a linked watch: the Watches tab, scrolled to its card. */
  function onOpenWatch(id: number) {
    go("watches");
    setTimeout(() => document.getElementById(`watch-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 80);
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
    go("search");
    await launch(`Checking watch ${w ? `${w.origin} → ${w.destination}` : ""}`, () => startWatchRun(id));
  }

  /** "Run again" on an interrupted job: re-submit what it stored. Never automatic. */
  const interruptedRerun = interrupted ? rerunTarget(interrupted) : null;
  function onRunAgain() {
    if (!interruptedRerun) return;
    if (interruptedRerun.type === "search") {
      const req = interruptedRerun.request;
      setForm((f) => requestToForm(req, f));
      performSearch(req);
    } else onRunWatch(interruptedRerun.watchId);
  }

  async function onWatch() {
    const watch = formToWatch(form);
    if (!form.origin.trim() || !form.destination.trim()) {
      toast("error", "Fill in where you're flying from and to first.");
      return;
    }
    if (!watch) {
      toast("error", "Set a max total price — you'll be alerted when a deal is at or below it.");
      setPriceNudge((n) => n + 1);
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
    setPriceNudge((n) => n + 1);
    toast("info", "Fill in the route and a max price, then tap “Track prices”.");
  }

  if (locked) return <LockScreen onUnlock={unlock} />;

  const section = sectionOf(tab);
  const counts: Partial<Record<Tab, number>> = { history: groups.length, watches: watches.length };
  // Watches whose last check met their target: the one number worth a badge in the header.
  const hits = watches.filter(
    (w) => w.active && !w.credits?.dates_passed && alertSettingsOf(w).threshold && w.last_best_price != null && w.last_best_price <= w.max_price
  ).length;
  const guideTitle = pickGuide(guides, todayISO())?.title;
  const openSection = (s: Section) => go(s === "tracking" ? (lastTrackingTab() ?? (watches.length ? "watches" : "history")) : s);

  return (
    <TripsProvider onError={onTripsError}>
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <a className="brand" href="#search" aria-label="Flight Scout home" title="Flight Scout — an agent that hunts the web for cheap flights">
            <span className="brand-mark" aria-hidden="true">
              <Icon name="plane" size={18} />
            </span>
            <span className="brand-text">
              <strong>Flight Scout</strong>
            </span>
          </a>
          <nav className="tabs" aria-label="Sections">
            {NAV.map((n) => (
              <a
                key={n.section}
                href={`#${n.section === "tracking" ? "watches" : n.section}`}
                className={`tab${section === n.section ? " active" : ""}`}
                aria-current={section === n.section ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  openSection(n.section);
                }}
              >
                <span className="tab-icon">
                  <Icon name={n.icon} size={20} />
                  {n.section === "search" && run && <span className="tab-live" aria-label="search running" />}
                </span>
                <span className="tab-label">{(n.section === "guide" && guideTitle) || n.label}</span>
                {n.section === "tracking" && hits > 0 && (
                  <span className="tab-count tab-count-hit" title={`${hits} watch${hits > 1 ? "es" : ""} at or under target`}>
                    {hits}
                  </span>
                )}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <main className="container">
        {wakingSince !== null && <WakeBanner since={wakingSince} />}
        {run && tab !== "search" && (
          <button className="live-banner" onClick={() => go("search")}>
            <span className="agent-dot" aria-hidden="true" /> {run.title}… <span className="live-banner-cta">View live</span>
          </button>
        )}
        {section === "tracking" && (
          <nav className="subnav" aria-label="Tracking">
            {TRACKING.map((t) => (
              <a
                key={t.tab}
                href={`#${t.tab}`}
                className={`subnav-tab${tab === t.tab ? " active" : ""}`}
                aria-current={tab === t.tab ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(t.tab);
                }}
              >
                <Icon name={t.icon} size={16} />
                {t.label}
                {!!counts[t.tab] && <span className="subnav-count">{counts[t.tab]}</span>}
              </a>
            ))}
          </nav>
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
            groups={groups}
            watches={watches}
            onPickRecent={onPickRecent}
            onOpenHistory={() => go("history")}
            onCreateWatch={onCreateWatchFrom}
            onEditWatch={onEditWatch}
            priceNudge={priceNudge}
            interrupted={interrupted}
            onRunAgain={interruptedRerun ? onRunAgain : undefined}
            onDismissInterrupted={() => setInterrupted(null)}
            onCheckLive={run ? undefined : onCheckLive}
          />
        )}
        {tab === "explore" && (
          <Suspense fallback={<p className="muted small">Loading Explore…</p>}>
            <ExploreTab
              busy={!!run}
              onCheckLive={(req) => {
                setForm((f) => requestToForm(req, f)); // the Search tab shows exactly what ran
                go("search");
                performSearch(req);
              }}
              onCreateWatch={onCreateWatchFrom}
              onError={handleRunError}
            />
          </Suspense>
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
        {tab === "trips" && (
          <TripsTab busy={!!run} onRerunSearch={onRerunDeal} onRunWatch={onRunWatch} onOpenWatch={onOpenWatch} onError={onTripsError} />
        )}
        {tab === "guide" && (
          <GuideTab
            guides={guides}
            loaded={guidesLoaded}
            onReload={async () => applyGuides(await fetchGuides())}
            onError={onTripsError}
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
    </TripsProvider>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <FlightScout />
    </ToastProvider>
  );
}
