"""FastAPI app: trigger agent searches and serve stored deals."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import agent, storage
from .models import SearchRequest, SearchResponse, StoredDeal

app = FastAPI(title="Flight Scout", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchResponse)
def run_search(req: SearchRequest) -> SearchResponse:
    """Run the flight-finding agent. Synchronous by design — the caller waits.

    A search takes on the order of a minute (the model runs several live web
    searches). FastAPI runs sync endpoints in a threadpool, so the server
    stays responsive while a search is in flight.
    """
    query = f"{req.origin} -> {req.destination} ({req.dates_hint})"
    try:
        deals = agent.find_deals(req.origin, req.destination, req.dates_hint)
    except Exception as exc:  # surface agent/API failures to the client
        raise HTTPException(status_code=502, detail=f"Agent search failed: {exc}") from exc
    stored = storage.save_deals(query, deals)
    return SearchResponse(query=query, deals=stored)


@app.get("/api/deals", response_model=list[StoredDeal])
def get_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 100,
) -> list[StoredDeal]:
    return storage.list_deals(origin, destination, max_price, limit)
