import type { Job, RouteHistory, SearchRequest, StoredDeal, UsageReport, Watch, WatchCreate, WatchUpdate } from "./types";
import type { DealDetails } from "./types";

const PASSWORD_KEY = "fs_app_password";

export class AuthError extends Error {
  constructor() {
    super("App password required");
  }
}

/** A non-2xx answer, with its status (404 on a job = the server doesn't know it). */
export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string
  ) {
    super(message);
  }
}

// In-memory copy so auth still works when storage is unavailable (private mode).
let memoryPassword: string | null = null;

export function setAppPassword(password: string): void {
  memoryPassword = password;
  try {
    localStorage.setItem(PASSWORD_KEY, password);
  } catch {
    // keep the in-memory copy only
  }
}

export function clearAppPassword(): void {
  memoryPassword = null;
  try {
    localStorage.removeItem(PASSWORD_KEY);
  } catch {
    // nothing stored
  }
}

function authHeaders(): Record<string, string> {
  let password = memoryPassword;
  try {
    password ??= localStorage.getItem(PASSWORD_KEY);
  } catch {
    // storage unavailable
  }
  return password ? { Authorization: `Bearer ${password}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    throw new AuthError();
  }
  if (!res.ok) {
    const body = await res.text();
    throw new HttpError(res.status, `${res.status}: ${body || res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Every stored deal, one row per sighting (no dedupe), so each search run keeps
 *  its full result set — the History tab folds runs and counts repeats itself. */
export async function fetchDeals(): Promise<StoredDeal[]> {
  return handle(await fetch("/api/deals?dedupe=false&limit=2000", { headers: authHeaders() }));
}

export async function startSearch(req: SearchRequest): Promise<Job> {
  return handle(
    await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(req),
    })
  );
}

export async function getJob(jobId: string): Promise<Job> {
  return handle(await fetch(`/api/jobs/${jobId}`, { headers: authHeaders() }));
}

export interface AgentEvent {
  kind: "phase" | "delta" | "search" | "end";
  text: string;
  // kind=search from a fare API: "google" (SerpApi Flights/Explore) or "aviasales";
  // absent = the LLM agent's own web search.
  source?: "google" | "aviasales";
}

/** Is the server up? A sleeping free instance takes ~1 min to answer, so each
 *  try gives up after `timeoutMs` (the caller retries). Needs no password. */
export async function pingHealth(timeoutMs = 15_000): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch("/api/health", { cache: "no-store", signal: ctrl.signal });
    // A host's own "waking up" page is not our JSON — keep waiting.
    return res.ok && ((await res.json()) as { status?: string }).status === "ok";
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/** Follow a job's live progress stream (SSE), from event index `after`.
 *  Resolves when the stream ends. Best-effort: callers treat a failure as
 *  "no live view", never as job failure. */
export async function streamJobEvents(jobId: string, onEvent: (e: AgentEvent) => void, after = 0): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}/events${after ? `?after=${after}` : ""}`, { headers: authHeaders() });
  if (!res.ok || !res.body) throw new Error(`${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const data = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!data) continue;
      try {
        onEvent(JSON.parse(data.slice(6)) as AgentEvent);
      } catch {
        // malformed frame — skip
      }
    }
  }
}

/** Ask a running job to stop; it aborts at its next progress emission. */
export async function cancelJob(jobId: string): Promise<Job> {
  return handle(await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST", headers: authHeaders() }));
}

const POLL_MS = 3000;
const RECONNECT_GIVE_UP_MS = 5 * 60_000;

/** Follow a job to completion: stream live progress (best-effort) while
 *  polling as the source of truth. The stream replays everything so far, so
 *  this also re-attaches to a job after a page reload.
 *
 *  Polls that fail (a deploy restarting the server, a network blip) are
 *  retried for a few minutes: jobs are stored server-side, so once the server
 *  is back it reports this one — finished, or "interrupted" if the restart
 *  killed it. Auth errors and 404 (unknown job) still throw at once. */
export async function followJob(job: Job, onEvent?: (e: AgentEvent) => void): Promise<Job> {
  let received = 0;
  let streaming = false;
  const stream = () => {
    if (!onEvent || streaming) return;
    streaming = true;
    streamJobEvents(
      job.job_id,
      (e) => {
        if (e.kind !== "end") received++;
        onEvent(e);
      },
      received
    )
      .catch(() => {})
      .finally(() => (streaming = false));
  };
  stream();
  let failingSince: number | null = null;
  for (;;) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    let current: Job;
    try {
      current = await getJob(job.job_id);
    } catch (e) {
      if (e instanceof AuthError || (e instanceof HttpError && e.status === 404)) throw e;
      if (failingSince === null) {
        failingSince = Date.now();
        onEvent?.({ kind: "phase", text: "Lost contact with the server (restarting?) — reconnecting…" });
      } else if (Date.now() - failingSince > RECONNECT_GIVE_UP_MS) {
        throw new Error("Lost contact with the server. Reload the page to check on this run.");
      }
      continue;
    }
    if (failingSince !== null) {
      failingSince = null;
      if (current.status === "running") {
        onEvent?.({ kind: "phase", text: "Reconnected." });
        stream(); // picks up after the events already shown
      }
    }
    if (current.status !== "running") return current;
  }
}

/** Start a single-watch run job (does not wait for completion). */
export async function startWatchRun(id: number): Promise<Job> {
  return handle(await fetch(`/api/watches/${id}/run`, { method: "POST", headers: authHeaders() }));
}

export async function fetchHistory(): Promise<RouteHistory[]> {
  return handle(await fetch("/api/history", { headers: authHeaders() }));
}

export async function fetchWatches(): Promise<Watch[]> {
  return handle(await fetch("/api/watches", { headers: authHeaders() }));
}

export async function createWatch(watch: WatchCreate): Promise<Watch> {
  return handle(
    await fetch("/api/watches", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(watch),
    })
  );
}

/** Partial update: edit criteria / threshold, or pause/resume via `active`. */
export async function updateWatch(id: number, patch: WatchUpdate): Promise<Watch> {
  return handle(
    await fetch(`/api/watches/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(patch),
    })
  );
}

export async function deleteWatch(id: number): Promise<void> {
  return handle(await fetch(`/api/watches/${id}`, { method: "DELETE", headers: authHeaders() }));
}

/** Fare-API calls this month (live Google Flights budget) — for the usage meter. */
export async function fetchUsage(): Promise<UsageReport> {
  return handle(await fetch("/api/usage", { headers: authHeaders() }));
}

/** A deal's cached return flight + sellers, or what fetching them would cost. Free. */
export async function fetchDealDetails(id: number): Promise<DealDetails> {
  return handle(await fetch(`/api/deals/${id}/details`, { headers: authHeaders() }));
}

/** Fetch a deal's details live — spends up to 2 live searches of the monthly budget. */
export async function loadDealDetails(id: number): Promise<DealDetails> {
  return handle(await fetch(`/api/deals/${id}/details`, { method: "POST", headers: authHeaders() }));
}
