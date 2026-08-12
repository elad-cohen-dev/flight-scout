import type { SearchJob, SearchRequest, StoredDeal } from "./types";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchDeals(): Promise<StoredDeal[]> {
  const res = await fetch("/api/deals");
  return handle<StoredDeal[]>(res);
}

export async function startSearch(req: SearchRequest): Promise<SearchJob> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handle<SearchJob>(res);
}

export async function getSearchJob(jobId: string): Promise<SearchJob> {
  const res = await fetch(`/api/search/${jobId}`);
  return handle<SearchJob>(res);
}

/** Start a search and poll until it finishes (or fails). */
export async function runSearch(req: SearchRequest): Promise<SearchJob> {
  const job = await startSearch(req);
  for (;;) {
    await new Promise((r) => setTimeout(r, 3000));
    const current = await getSearchJob(job.job_id);
    if (current.status !== "running") return current;
  }
}
