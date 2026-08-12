export interface StoredDeal {
  id: number;
  search_query: string;
  found_at: string;
  origin: string;
  destination: string;
  airline: string;
  price: number;
  currency: string;
  depart_date: string;
  return_date: string;
  duration: string;
  stops: number;
  booking_url: string;
  source_url: string;
  notes: string;
}

export interface SearchRequest {
  origin: string;
  destination: string;
  dates_hint: string;
}

export interface Job {
  job_id: string;
  kind: "search" | "watch_run";
  status: "running" | "done" | "error";
  query: string;
  deals: StoredDeal[] | null;
  summary: Record<string, unknown> | null;
  error: string | null;
}

export interface Watch {
  id: number;
  origin: string;
  destination: string;
  dates_hint: string;
  max_price: number;
  currency: string;
  active: boolean;
  created_at: string;
  last_run_at: string | null;
  last_best_price: number | null;
}

export interface WatchCreate {
  origin: string;
  destination: string;
  dates_hint: string;
  max_price: number;
}

/** Deals grouped by search run (same query + same found_at timestamp). */
export interface SearchGroup {
  key: string;
  query: string;
  foundAt: string;
  deals: StoredDeal[];
  minPrice: number;
}
