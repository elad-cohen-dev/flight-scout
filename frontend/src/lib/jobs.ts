import type { Job, SearchRequest } from "../types";

/** The job this tab is following, remembered so a reload re-attaches to it. */
export interface ActiveJob {
  job_id: string;
  kind: Job["kind"];
  title: string;
}

const ACTIVE_JOB_KEY = "fs_active_job";

type KeyValueStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function defaultStore(): KeyValueStore | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null; // storage blocked (private mode, sandboxed frame)
  }
}

export function rememberJob(job: ActiveJob, store: KeyValueStore | null = defaultStore()): void {
  try {
    store?.setItem(ACTIVE_JOB_KEY, JSON.stringify(job));
  } catch {
    // best-effort: without storage a reload just won't re-attach
  }
}

export function loadActiveJob(store: KeyValueStore | null = defaultStore()): ActiveJob | null {
  try {
    const parsed = JSON.parse(store?.getItem(ACTIVE_JOB_KEY) ?? "null") as Partial<ActiveJob> | null;
    if (parsed && typeof parsed.job_id === "string" && (parsed.kind === "search" || parsed.kind === "watch_run")) {
      return { job_id: parsed.job_id, kind: parsed.kind, title: String(parsed.title ?? "") };
    }
  } catch {
    // unreadable — treat as nothing remembered
  }
  return null;
}

export function forgetJob(jobId?: string, store: KeyValueStore | null = defaultStore()): void {
  try {
    if (jobId && loadActiveJob(store)?.job_id !== jobId) return; // a newer job took the slot
    store?.removeItem(ACTIVE_JOB_KEY);
  } catch {
    // nothing stored
  }
}

export type RerunTarget = { type: "search"; request: SearchRequest } | { type: "watch"; watchId: number };

/** What "Run again" re-submits for an interrupted job: its stored search
 *  request, or the watch it checked. Null for runs the UI can't start (the
 *  scheduled sweep, which needs the cron secret). */
export function rerunTarget(job: Pick<Job, "kind" | "request">): RerunTarget | null {
  const req = job.request;
  if (!req) return null;
  if (job.kind === "search" && typeof req.origin === "string" && typeof req.destination === "string") {
    return { type: "search", request: req as unknown as SearchRequest };
  }
  if (job.kind === "watch_run" && typeof req.watch_id === "number") return { type: "watch", watchId: req.watch_id };
  return null;
}

/** Wait until the server answers its health check. Free hosting sleeps when
 *  idle and takes about a minute to wake, so `onSlow` fires once if the first
 *  answer hasn't come within `slowAfterMs` — the cue for the wake-up banner.
 *  Retries forever; resolves with whether it was slow. */
export async function waitForServer({
  ping,
  onSlow,
  slowAfterMs = 3000,
  retryMs = 2000,
  sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms)),
}: {
  ping: () => Promise<boolean>;
  onSlow?: () => void;
  slowAfterMs?: number;
  retryMs?: number;
  sleep?: (ms: number) => Promise<void>;
}): Promise<boolean> {
  let slow = false;
  const timer = setTimeout(() => {
    if (slow) return;
    slow = true;
    onSlow?.();
  }, slowAfterMs);
  try {
    for (;;) {
      if (await ping().catch(() => false)) return slow;
      if (!slow) {
        slow = true; // an outright failure is as good a cue as a slow answer
        onSlow?.();
      }
      await sleep(retryMs);
    }
  } finally {
    clearTimeout(timer);
  }
}

/** Rough wake-up progress for the banner: a free instance usually needs about
 *  a minute, so fill towards 95% over `expectedMs`, never claiming done. */
export function wakeProgress(elapsedMs: number, expectedMs = 60_000): number {
  return Math.min(0.95, Math.max(0, elapsedMs / expectedMs) * 0.95);
}
