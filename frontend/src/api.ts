import type { SearchRequest, SearchResponse, StoredDeal } from "./types";

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

export async function runSearch(req: SearchRequest): Promise<SearchResponse> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handle<SearchResponse>(res);
}
