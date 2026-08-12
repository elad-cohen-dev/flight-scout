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

/** Start a search and poll until it finishes (or fails). */
export async function runSearch(req: SearchRequest): Promise<Job> {
  const job = await startSearch(req);
  for (;;) {
    await new Promise((r) => setTimeout(r, 3000));
    const current = await getJob(job.job_id);
    if (current.status !== "running") return current;
  }
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
