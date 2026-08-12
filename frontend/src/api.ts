import type { Job, SearchRequest, StoredDeal, Watch, WatchCreate } from "./types";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body || res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function fetchDeals(): Promise<StoredDeal[]> {
  return handle(await fetch("/api/deals"));
}

export async function startSearch(req: SearchRequest): Promise<Job> {
  return handle(
    await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    })
  );
}

export async function getJob(jobId: string): Promise<Job> {
  return handle(await fetch(`/api/jobs/${jobId}`));
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

export async function fetchWatches(): Promise<Watch[]> {
  return handle(await fetch("/api/watches"));
}

export async function createWatch(watch: WatchCreate): Promise<Watch> {
  return handle(
    await fetch("/api/watches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(watch),
    })
  );
}

export async function deleteWatch(id: number): Promise<void> {
  return handle(await fetch(`/api/watches/${id}`, { method: "DELETE" }));
}
