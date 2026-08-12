"""FastAPI app: agent searches and watch runs as background jobs, deals + watches API.

Long work (searches ~1-2 min each, watch runs potentially several searches) never
blocks a request: POST endpoints return a job id immediately and clients poll
GET /api/jobs/{id}. Jobs run in daemon threads and live in memory — fine for a
single instance; a restart loses in-flight jobs but never stored data.

In production the built frontend (static files) is served from STATIC_DIR.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, storage, watcher
from .models import SearchRequest, StoredDeal, Watch, WatchCreate

app = FastAPI(title="Flight Scout", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- background jobs -------------------------------------------------------

class Job(BaseModel):
    job_id: str
    kind: Literal["search", "watch_run"]
    status: Literal["running", "done", "error"]
    query: str
    deals: list[StoredDeal] | None = None   # set for kind=search
    summary: dict | None = None             # set for kind=watch_run
    error: str | None = None


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _start_job(kind: Literal["search", "watch_run"], query: str, work: Callable[[], dict]) -> Job:
    """Register a job and run `work` in a daemon thread; its dict return updates the job."""
    job = Job(job_id=uuid.uuid4().hex, kind=kind, status="running", query=query)
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job

    def _run() -> None:
        try:
            update = {"status": "done", **work()}
        except Exception as exc:
            update = {"status": "error", "error": str(exc)}
        with _JOBS_LOCK:
            _JOBS[job.job_id] = _JOBS[job.job_id].model_copy(update=update)

    threading.Thread(target=_run, daemon=True).start()
    return job


@app.get("/api/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id (server may have restarted)")
    return job


# --- searches ---------------------------------------------------------------

@app.post("/api/search", response_model=Job, status_code=202)
def start_search(req: SearchRequest) -> Job:
    query = f"{req.origin} -> {req.destination} ({req.dates_hint})"

    def work() -> dict:
        deals = agent.find_deals(req.origin, req.destination, req.dates_hint)
        return {"deals": storage.save_deals(query, deals)}

    return _start_job("search", query, work)


@app.get("/api/deals", response_model=list[StoredDeal])
def get_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 200,
) -> list[StoredDeal]:
    return storage.list_deals(origin, destination, max_price, limit)


# --- watches ----------------------------------------------------------------

@app.post("/api/watches", response_model=Watch, status_code=201)
def create_watch(watch: WatchCreate) -> Watch:
    return storage.add_watch(watch)


@app.get("/api/watches", response_model=list[Watch])
def get_watches() -> list[Watch]:
    return storage.list_watches()


@app.delete("/api/watches/{watch_id}", status_code=204)
def remove_watch(watch_id: int) -> None:
    if not storage.delete_watch(watch_id):
        raise HTTPException(status_code=404, detail="No such watch")


@app.post("/api/watches/run", response_model=Job, status_code=202)
def run_watches(x_cron_secret: str | None = Header(default=None)) -> Job:
    """Re-search all active watches and alert on threshold hits.

    Called by the scheduled GitHub Actions workflow. Protected by CRON_SECRET
    when set (always set it in production — each run spends LLM credit).
    """
    expected = os.environ.get("CRON_SECRET")
    if expected and x_cron_secret != expected:
        raise HTTPException(status_code=401, detail="Bad or missing X-Cron-Secret header")
    return _start_job("watch_run", "scheduled watch run", lambda: {"summary": watcher.run_watches()})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Serve the built frontend in production (must be mounted after the API routes).
_static_dir = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
