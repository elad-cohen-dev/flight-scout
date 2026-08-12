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

export interface SearchJob {
  job_id: string;
  status: "running" | "done" | "error";
  query: string;
  deals: StoredDeal[] | null;
  error: string | null;
}
