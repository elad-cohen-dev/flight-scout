"""FastAPI app: trigger agent searches (as background jobs) and serve stored deals.

Searches take 1-2 minutes, longer than most hosting proxies allow a request to
hang — so POST /api/search returns a job id immediately and the client polls
GET /api/search/{job_id}. Jobs run in a daemon thread and live in memory:
fine for a single instance; a restart loses in-flight jobs but never stored deals.

In production the built frontend (static files) is served from STATIC_DIR.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, storage
from .models import SearchRequest, StoredDeal

app = FastAPI(title="Flight Scout", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchJob(BaseModel):
    job_id: str
    status: Literal["running", "done", "error"]
    query: str
    deals: list[StoredDeal] | None = None
    error: str | None = None


_JOBS: dict[str, SearchJob] = {}
_JOBS_LOCK = threading.Lock()


def _run_job(job_id: str, req: SearchRequest, query: str) -> None:
    try:
        deals = agent.find_deals(req.origin, req.destination, req.dates_hint)
        stored = storage.save_deals(query, deals)
        update = {"status": "done", "deals": stored}
    except Exception as exc:
        update = {"status": "error", "error": str(exc)}
    with _JOBS_LOCK:
        _JOBS[job_id] = _JOBS[job_id].model_copy(update=update)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchJob, status_code=202)
def start_search(req: SearchRequest) -> SearchJob:
    query = f"{req.origin} -> {req.destination} ({req.dates_hint})"
    job = SearchJob(job_id=uuid.uuid4().hex, status="running", query=query)
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
    threading.Thread(target=_run_job, args=(job.job_id, req, query), daemon=True).start()
    return job


@app.get("/api/search/{job_id}", response_model=SearchJob)
def get_search(job_id: str) -> SearchJob:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id (server may have restarted)")
    return job


@app.get("/api/deals", response_model=list[StoredDeal])
def get_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 100,
) -> list[StoredDeal]:
    return storage.list_deals(origin, destination, max_price, limit)


# Serve the built frontend in production (must be mounted after the API routes).
_static_dir = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
