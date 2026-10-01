"""Background jobs: searches and watch runs in daemon threads, restart-safe.

Each job lives in memory while this process runs it (the hot path: polling and
the SSE stream read only memory) and is mirrored to the `jobs` / `job_events`
tables, so a deploy or restart mid-run doesn't turn it into "unknown job id":

- The job row is written when the thread starts and again when it finishes
  (status + result) — two writes per job.
- Progress events are buffered and written in batches by one flusher thread,
  every FLUSH_INTERVAL seconds (sooner past FLUSH_AT_EVENTS). Consecutive
  research deltas are coalesced first, so a search streaming thousands of
  tokens costs a write every few seconds, not one per token. At most
  MAX_STORED_EVENTS are stored per job.
- On startup, rows a dead process left "running" become "interrupted"; nothing
  re-runs by itself (a search can spend a live fare-API credit or LLM money),
  the UI offers "Run again" from the stored request instead.

Persistence is best-effort throughout: a database hiccup is logged and the job
carries on in memory exactly as before.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterator, Literal

from pydantic import BaseModel

from . import agent, storage
from .models import SearchMeta, StoredDeal

logger = logging.getLogger(__name__)

JobKind = Literal["search", "watch_run"]
JobStatus = Literal["running", "done", "error", "cancelled", "interrupted"]


class Job(BaseModel):
    job_id: str
    kind: JobKind
    status: JobStatus
    query: str
    deals: list[StoredDeal] | None = None   # set for kind=search
    summary: dict | None = None             # set for kind=watch_run
    meta: SearchMeta | None = None          # kind=search: sources used, price insights, notices
    error: str | None = None
    request: dict | None = None             # what started it, so the UI can offer "Run again"
    created_at: str | None = None
    updated_at: str | None = None


SEARCH_INTERRUPTED = ("Interrupted: the server restarted (a deploy or a crash) while this search "
                      "was running. Nothing was saved, and it won't re-run by itself.")
WATCH_INTERRUPTED = ("Interrupted: the server restarted (a deploy or a crash) during this watch run. "
                     "Watches checked before the restart kept their results; the rest were skipped.")

_RESULT_FIELDS = {"deals", "summary", "meta"}

_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS_IN_MEMORY = 200   # finished jobs past this are dropped (still in the DB)

# Live progress events per job ({"kind": "phase"|"delta"|"search", "text": ...}),
# appended by the job thread and streamed to clients by /api/jobs/{id}/events.
_JOB_EVENTS: dict[str, list[dict]] = {}
_MAX_EVENTS_PER_JOB = 5000

# Jobs the user asked to stop. The job thread notices at its next progress
# emission (sub-second during streaming research) and aborts without saving.
_CANCEL_REQUESTED: set[str] = set()

# Events emitted but not yet written, per job, and how many each job has stored.
FLUSH_INTERVAL = 5.0
FLUSH_AT_EVENTS = 100
MAX_STORED_EVENTS = 1000
_MAX_DELTA_CHARS = 4000     # coalesced research text per stored event
_PENDING: dict[str, list[dict]] = {}
_STORED: dict[str, int] = {}       # events stored so far
_BATCHES: dict[str, int] = {}      # batches stored so far (the next batch's seq)
_flush_lock = threading.Lock()     # one flush at a time keeps batches in order
_flush_wake = threading.Event()
_flusher: threading.Thread | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- persistence --------------------------------------------------------------

def _row(job: Job) -> dict:
    result = job.model_dump(mode="json", include=_RESULT_FIELDS, exclude_none=True)
    return {
        "job_id": job.job_id, "kind": job.kind, "status": job.status, "query": job.query,
        "request": json.dumps(job.request) if job.request is not None else None,
        "result": json.dumps(result) if result else None,
        "error": job.error, "created_at": job.created_at or _now(), "updated_at": job.updated_at or _now(),
    }


def _from_row(row: dict) -> Job:
    fields = {k: row[k] for k in ("job_id", "kind", "status", "query", "error", "created_at", "updated_at")}
    for column in ("request", "result"):
        try:
            decoded = json.loads(row[column]) if row.get(column) else None
        except Exception:
            decoded = None
        if column == "request":
            fields["request"] = decoded if isinstance(decoded, dict) else None
        elif isinstance(decoded, dict):
            fields.update({k: v for k, v in decoded.items() if k in _RESULT_FIELDS})
    return Job(**fields)


def _persist(job: Job) -> None:
    try:
        storage.save_job(_row(job))
    except Exception:
        logger.exception("could not persist job %s (%s)", job.job_id, job.status)


def _buffer(job_id: str, event: dict) -> None:
    """Queue an event for the next flush (caller holds _JOBS_LOCK)."""
    pending = _PENDING.get(job_id)
    if pending is None:  # not a persisted job, or already finished
        return
    last = pending[-1] if pending else None
    if (event.get("kind") == "delta" and last is not None and last.get("kind") == "delta"
            and len(last.get("text", "")) < _MAX_DELTA_CHARS):
        pending[-1] = {**last, "text": last.get("text", "") + str(event.get("text", ""))}
        return
    if _STORED.get(job_id, 0) + len(pending) >= MAX_STORED_EVENTS:
        return
    pending.append(dict(event))
    if len(pending) >= FLUSH_AT_EVENTS:
        _flush_wake.set()


def flush_events(job_id: str | None = None) -> None:
    """Write buffered events — of one job, or of every job — to the database."""
    with _flush_lock:
        with _JOBS_LOCK:
            ids = [job_id] if job_id is not None else list(_PENDING)
            batches = []
            for jid in ids:
                pending = _PENDING.get(jid)
                if pending:
                    batches.append((jid, _BATCHES.get(jid, 0), pending))
                    _PENDING[jid] = []
                    _BATCHES[jid] = _BATCHES.get(jid, 0) + 1
                    _STORED[jid] = _STORED.get(jid, 0) + len(pending)
        for jid, seq, events in batches:
            try:
                storage.append_job_events(jid, seq, events)
            except Exception:
                logger.exception("could not persist %d events of job %s", len(events), jid)


def _flush_loop() -> None:
    while True:
        _flush_wake.wait(FLUSH_INTERVAL)
        _flush_wake.clear()
        flush_events()


def _ensure_flusher() -> None:
    global _flusher
    with _JOBS_LOCK:
        if _flusher is None or not _flusher.is_alive():
            _flusher = threading.Thread(target=_flush_loop, name="job-events-flusher", daemon=True)
            _flusher.start()


def recover_interrupted() -> list[str]:
    """Startup: jobs still "running" in the DB belong to a dead process."""
    try:
        ids = storage.interrupt_running_jobs(SEARCH_INTERRUPTED, WATCH_INTERRUPTED)
    except Exception:
        logger.exception("could not mark interrupted jobs")
        return []
    if ids:
        logger.warning("marked %d job(s) interrupted by a restart: %s", len(ids), ", ".join(ids))
    return ids


def shutdown() -> None:
    """Graceful shutdown: this process's running jobs won't finish — say so now
    (startup would catch them too, but only once a new process is up)."""
    flush_events()
    with _JOBS_LOCK:
        running = [j for j in _JOBS.values() if j.status == "running"]
    for job in running:
        message = WATCH_INTERRUPTED if job.kind == "watch_run" else SEARCH_INTERRUPTED
        _persist(job.model_copy(update={"status": "interrupted", "error": message, "updated_at": _now()}))


# --- running jobs ---------------------------------------------------------------

def emit_event(job_id: str, event: dict) -> None:
    with _JOBS_LOCK:
        events = _JOB_EVENTS.setdefault(job_id, [])
        if len(events) < _MAX_EVENTS_PER_JOB:
            events.append(event)
            _buffer(job_id, event)


def emit_or_cancel(job_id: str, event: dict) -> None:
    """Progress callback for cancellable jobs: emission points double as
    cancellation points (see agent.SearchCancelled)."""
    with _JOBS_LOCK:
        if job_id in _CANCEL_REQUESTED:
            raise agent.SearchCancelled()
    emit_event(job_id, event)


def _evict_finished() -> None:
    """Keep memory bounded (caller holds _JOBS_LOCK); evicted jobs stay in the DB."""
    excess = len(_JOBS) - _MAX_JOBS_IN_MEMORY
    if excess <= 0:
        return
    for jid in [jid for jid, j in _JOBS.items() if j.status != "running"][:excess]:
        _JOBS.pop(jid, None)
        _JOB_EVENTS.pop(jid, None)


def _register(kind: JobKind, query: str, request: dict | None) -> Job:
    now = _now()
    job = Job(job_id=uuid.uuid4().hex, kind=kind, status="running", query=query,
              request=request, created_at=now, updated_at=now)
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
        _PENDING[job.job_id] = []
    _ensure_flusher()
    return job


def _finish(job: Job, update: dict) -> Job:
    with _JOBS_LOCK:
        final = _JOBS.get(job.job_id, job).model_copy(update={**update, "updated_at": _now()})
    # Durable first, so a client that sees the result in memory can always
    # find it again after a restart.
    flush_events(job.job_id)
    _persist(final)
    with _JOBS_LOCK:
        _JOBS[job.job_id] = final
        _CANCEL_REQUESTED.discard(job.job_id)
        for table in (_PENDING, _STORED, _BATCHES):
            table.pop(job.job_id, None)
        _evict_finished()
    return final


def _execute(job: Job, work: Callable[[str], dict]) -> Job:
    _persist(job)
    try:
        update = {"status": "done", **work(job.job_id)}
    except agent.SearchCancelled:
        update = {"status": "cancelled"}
    except Exception as exc:
        update = {"status": "error", "error": str(exc)}
    return _finish(job, update)


def start_job(kind: JobKind, query: str, work: Callable[[str], dict],
              request: dict | None = None) -> Job:
    """Register a job and run `work(job_id)` in a daemon thread; its dict return updates the job."""
    job = _register(kind, query, request)
    threading.Thread(target=_execute, args=(job, work), daemon=True).start()
    return job


def run_job(kind: JobKind, query: str, work: Callable[[str], dict],
            request: dict | None = None, interrupted: str | None = None) -> Job:
    """Run `work(job_id)` in the calling thread and return the finished job —
    the same rows and events as start_job, without a web server (the scheduled
    sweep runs as a GitHub Actions script, scripts/run_watches.py).

    A KeyboardInterrupt / SystemExit (the runner cancelling or timing out the
    step) records the job as "interrupted" with `interrupted` as its error,
    then propagates."""
    job = _register(kind, query, request)
    try:
        return _execute(job, work)
    except BaseException:
        message = interrupted or (WATCH_INTERRUPTED if kind == "watch_run" else SEARCH_INTERRUPTED)
        _finish(job, {"status": "interrupted", "error": message})
        raise


def get_job(job_id: str) -> Job | None:
    """The live job, else its stored record (e.g. from before a restart)."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        return job
    try:
        row = storage.get_job(job_id)
    except Exception:
        logger.exception("could not load job %s", job_id)
        return None
    return _from_row(row) if row else None


def cancel(job_id: str) -> Job | None:
    """Request a running job to stop. Only this process can stop its own jobs;
    a stored job that isn't running here is returned unchanged."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job.status == "running":
            _CANCEL_REQUESTED.add(job_id)
    if job is None:
        return get_job(job_id)
    if job.status == "running":
        emit_event(job_id, {"kind": "phase", "text": "Stopping…"})
    return job


def event_stream(job_id: str, after: int = 0) -> Iterator[dict] | None:
    """Events of a job from index `after`, following it live while it runs here,
    then a final {"kind": "end", "text": status}. None for an unknown job.

    A job that isn't in memory is replayed from the DB (its stored events are
    coalesced, so indices differ from the live stream's — `after` is only
    meaningful against the same process)."""
    with _JOBS_LOCK:
        live = job_id in _JOBS
    if not live:
        job = get_job(job_id)
        if job is None:
            return None
        try:
            stored = storage.get_job_events(job_id)
        except Exception:
            logger.exception("could not load events of job %s", job_id)
            stored = []
        return iter([*stored[after:], {"kind": "end", "text": job.status}])

    def gen() -> Iterator[dict]:
        sent = max(after, 0)
        while True:
            with _JOBS_LOCK:
                pending = _JOB_EVENTS.get(job_id, [])[sent:]
                job = _JOBS.get(job_id)
            yield from pending
            sent += len(pending)
            if job is None or job.status != "running":
                yield {"kind": "end", "text": job.status if job else "error"}
                return
            time.sleep(0.25)

    return gen()


def reset_state() -> None:
    """Forget every in-memory job — what a restart does. Tests only."""
    flush_events()
    with _JOBS_LOCK:
        for table in (_JOBS, _JOB_EVENTS, _PENDING, _STORED, _BATCHES):
            table.clear()
        _CANCEL_REQUESTED.clear()
