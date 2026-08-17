"""FastAPI app: agent searches and watch runs as background jobs, deals + watches API.

Long work (searches ~1-2 min each, watch runs potentially several searches) never
blocks a request: POST endpoints return a job id immediately and clients poll
GET /api/jobs/{id}. Jobs run in daemon threads and live in memory — fine for a
single instance; a restart loses in-flight jobs but never stored data.

In production the built frontend (static files) is served from STATIC_DIR.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, storage, watcher
from .models import RouteHistory, SearchRequest, StoredDeal, Watch, WatchCreate

app = FastAPI(title="Flight Scout", version="0.4.0")


def require_auth(
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
) -> None:
    """Gate API access behind APP_PASSWORD when it is set.

    Accepted credentials: `Authorization: Bearer <APP_PASSWORD>` (the web app),
    or a valid X-Cron-Secret (so the scheduled workflow can poll job status).
    With APP_PASSWORD unset the API is open — convenient for local dev.
    """
    app_password = os.environ.get("APP_PASSWORD")
    if not app_password:
        return
    if authorization and authorization.startswith("Bearer "):
        if secrets.compare_digest(authorization.removeprefix("Bearer "), app_password):
            return
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret and x_cron_secret and secrets.compare_digest(x_cron_secret, cron_secret):
        return
    raise HTTPException(status_code=401, detail="Invalid or missing app password")

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
    status: Literal["running", "done", "error", "cancelled"]
    query: str
    deals: list[StoredDeal] | None = None   # set for kind=search
    summary: dict | None = None             # set for kind=watch_run
    error: str | None = None


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()

# Live progress events per job ({"kind": "phase"|"delta"|"search", "text": ...}),
# appended by the job thread and streamed to clients by /api/jobs/{id}/events.
_JOB_EVENTS: dict[str, list[dict]] = {}
_MAX_EVENTS_PER_JOB = 5000

# Jobs the user asked to stop. The job thread notices at its next progress
# emission (sub-second during streaming research) and aborts without saving.
_CANCEL_REQUESTED: set[str] = set()


def _emit_event(job_id: str, event: dict) -> None:
    with _JOBS_LOCK:
        events = _JOB_EVENTS.setdefault(job_id, [])
        if len(events) < _MAX_EVENTS_PER_JOB:
            events.append(event)


def _emit_or_cancel(job_id: str, event: dict) -> None:
    """Progress callback for cancellable jobs: emission points double as
    cancellation points (see agent.SearchCancelled)."""
    with _JOBS_LOCK:
        if job_id in _CANCEL_REQUESTED:
            raise agent.SearchCancelled()
    _emit_event(job_id, event)


def _start_job(kind: Literal["search", "watch_run"], query: str, work: Callable[[str], dict]) -> Job:
    """Register a job and run `work(job_id)` in a daemon thread; its dict return updates the job."""
    job = Job(job_id=uuid.uuid4().hex, kind=kind, status="running", query=query)
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job

    def _run() -> None:
        try:
            update = {"status": "done", **work(job.job_id)}
        except agent.SearchCancelled:
            update = {"status": "cancelled"}
        except Exception as exc:
            update = {"status": "error", "error": str(exc)}
        with _JOBS_LOCK:
            _JOBS[job.job_id] = _JOBS[job.job_id].model_copy(update=update)
            _CANCEL_REQUESTED.discard(job.job_id)

    threading.Thread(target=_run, daemon=True).start()
    return job


@app.get("/api/jobs/{job_id}", response_model=Job, dependencies=[Depends(require_auth)])
def get_job(job_id: str) -> Job:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id (server may have restarted)")
    return job


@app.post("/api/jobs/{job_id}/cancel", response_model=Job, dependencies=[Depends(require_auth)])
def cancel_job(job_id: str) -> Job:
    """Request a running job to stop. Cooperative: the job aborts at its next
    progress emission and flips to status "cancelled" without saving anything."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id (server may have restarted)")
        if job.status == "running":
            _CANCEL_REQUESTED.add(job_id)
    if job.status == "running":
        _emit_event(job_id, {"kind": "phase", "text": "Stopping…"})
    return job


@app.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_auth)])
def stream_job_events(job_id: str) -> StreamingResponse:
    """Server-Sent Events: replay this job's progress so far, then follow it
    live until the job leaves the running state. The UI renders these as a
    streaming 'agent console'; polling /api/jobs/{id} remains the source of
    truth for completion, so a dropped stream degrades gracefully."""
    with _JOBS_LOCK:
        if job_id not in _JOBS:
            raise HTTPException(status_code=404, detail="Unknown job id (server may have restarted)")

    def gen():
        sent = 0
        while True:
            with _JOBS_LOCK:
                events = _JOB_EVENTS.get(job_id, [])
                pending = events[sent:]
                job = _JOBS.get(job_id)
            for event in pending:
                yield f"data: {json.dumps(event)}\n\n"
            sent += len(pending)
            if job is None or job.status != "running":
                status = job.status if job else "error"
                yield f"data: {json.dumps({'kind': 'end', 'text': status})}\n\n"
                return
            time.sleep(0.25)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- searches ---------------------------------------------------------------

@app.post("/api/search", response_model=Job, status_code=202, dependencies=[Depends(require_auth)])
def start_search(req: SearchRequest) -> Job:
    constraint = ", direct only" if req.direct_only else ""
    query = f"{req.origin} -> {req.destination} ({req.dates_hint}{constraint})"

    def work(job_id: str) -> dict:
        deals = agent.find_deals(
            req.origin, req.destination, req.dates_hint, req.direct_only,
            on_event=lambda event: _emit_or_cancel(job_id, event),
        )
        return {"deals": storage.save_deals(query, deals)}

    return _start_job("search", query, work)


@app.get("/api/deals", response_model=list[StoredDeal], dependencies=[Depends(require_auth)])
def get_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 200,
    dedupe: bool = True,
) -> list[StoredDeal]:
    """List deals. With dedupe (default), a deal re-found by later runs appears
    once — its latest sighting — carrying times_seen / first_found_at."""
    return storage.list_deals(origin, destination, max_price, limit, dedupe)


@app.get("/api/history", response_model=list[RouteHistory], dependencies=[Depends(require_auth)])
def get_history() -> list[RouteHistory]:
    """Best price per route per search run — the data behind the price chart."""
    return storage.get_route_history()


# --- watches ----------------------------------------------------------------

@app.post("/api/watches", response_model=Watch, status_code=201, dependencies=[Depends(require_auth)])
def create_watch(watch: WatchCreate) -> Watch:
    return storage.add_watch(watch)


@app.get("/api/watches", response_model=list[Watch], dependencies=[Depends(require_auth)])
def get_watches() -> list[Watch]:
    return storage.list_watches()


@app.delete("/api/watches/{watch_id}", status_code=204, dependencies=[Depends(require_auth)])
def remove_watch(watch_id: int) -> None:
    if not storage.delete_watch(watch_id):
        raise HTTPException(status_code=404, detail="No such watch")


@app.post("/api/watches/{watch_id}/run", response_model=Job, status_code=202,
          dependencies=[Depends(require_auth)])
def run_single_watch(watch_id: int) -> Job:
    """Run one watch immediately (the UI's "Run now" button) as a background
    job with live progress events — same alert logic as the daily sweep."""
    watch = next((w for w in storage.list_watches(active_only=True) if w.id == watch_id), None)
    if watch is None:
        raise HTTPException(status_code=404, detail="No such active watch")
    query = f"{watch.origin} -> {watch.destination} ({watch.dates_hint}) [watch #{watch.id}]"

    def work(job_id: str) -> dict:
        summary = watcher.run_watches(
            only_id=watch_id, on_event=lambda event: _emit_or_cancel(job_id, event)
        )
        return {"summary": summary}

    return _start_job("watch_run", query, work)


@app.post("/api/watches/run", response_model=Job, status_code=202)
def run_watches(x_cron_secret: str | None = Header(default=None)) -> Job:
    """Re-search all active watches and alert on threshold hits.

    Called by the scheduled GitHub Actions workflow. Protected by CRON_SECRET
    when set (always set it in production — each run spends LLM credit).
    """
    expected = os.environ.get("CRON_SECRET")
    if expected and x_cron_secret != expected:
        raise HTTPException(status_code=401, detail="Bad or missing X-Cron-Secret header")
    return _start_job("watch_run", "scheduled watch run", lambda _job_id: {"summary": watcher.run_watches()})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Serve the built frontend in production (must be mounted after the API routes).
_static_dir = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
