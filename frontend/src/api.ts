import type { Job, RouteHistory, SearchRequest, StoredDeal, Watch, WatchCreate } from "./types";

const PASSWORD_KEY = "fs_app_password";

export class AuthError extends Error {
  constructor() {
    super("App password required");
  }
}

export function setAppPassword(password: string): void {
  localStorage.setItem(PASSWORD_KEY, password);
}

export function clearAppPassword(): void {
  localStorage.removeItem(PASSWORD_KEY);
}

function authHeaders(): Record<string, string> {
  const password = localStorage.getItem(PASSWORD_KEY);
  return password ? { Authorization: `Bearer ${password}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    throw new AuthError();
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body || res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function fetchDeals(): Promise<StoredDeal[]> {
  return handle(await fetch("/api/deals", { headers: authHeaders() }));
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
}

/** Follow a job's live progress stream (SSE). Resolves when the stream ends.
 *  Best-effort: callers treat a failure as "no live view", never as job failure. */
export async function streamJobEvents(jobId: string, onEvent: (e: AgentEvent) => void): Promise<void> {
  const res = await fetch(`/api/jobs/${jobId}/events`, { headers: authHeaders() });
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

/** Follow a started job to completion: stream live progress (best-effort)
 *  while polling as the source of truth. */
async function followJob(job: Job, onEvent?: (e: AgentEvent) => void): Promise<Job> {
  if (onEvent) {
    streamJobEvents(job.job_id, onEvent).catch(() => {});
  }
  for (;;) {
    await new Promise((r) => setTimeout(r, 3000));
    const current = await getJob(job.job_id);
    if (current.status !== "running") return current;
  }
}

/** Start a search and poll until it finishes (or fails). */
export async function runSearch(req: SearchRequest, onEvent?: (e: AgentEvent) => void): Promise<Job> {
  return followJob(await startSearch(req), onEvent);
}

/** Run one watch immediately and poll until it finishes (or fails). */
export async function runWatchNow(id: number, onEvent?: (e: AgentEvent) => void): Promise<Job> {
  const job = await handle<Job>(
    await fetch(`/api/watches/${id}/run`, { method: "POST", headers: authHeaders() })
  );
  return followJob(job, onEvent);
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

export async function deleteWatch(id: number): Promise<void> {
  return handle(await fetch(`/api/watches/${id}`, { method: "DELETE", headers: authHeaders() }));
}
