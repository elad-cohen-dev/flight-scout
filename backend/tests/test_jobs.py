"""Restart-safe jobs (app/jobs.py): persistence, DB fallback, interrupted marking,
event batching, cancel and SSE replay. A "restart" is jobs.reset_state(), which
forgets every in-memory job exactly like a fresh process would."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import agent, jobs, main, storage
from app.models import FlightDeal


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    jobs.reset_state()
    yield TestClient(main.app)
    # No job thread may outlive the temp DB (it would write to the real one).
    wait_until(lambda: not any(j.status == "running" for j in list(jobs._JOBS.values())))
    jobs.reset_state()


def wait_until(check, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out")


def finished(client, job_id):
    return wait_until(lambda: (j := client.get(f"/api/jobs/{job_id}").json())["status"] != "running" and j)


def blocked_job(release: threading.Event, before=lambda job_id: None, kind="search"):
    def work(job_id):
        before(job_id)
        release.wait(5)
        return {"summary": {"ok": True}}

    return jobs.start_job(kind, "TLV -> Rome (November)", work, request={"origin": "TLV"})


def sse_events(client, job_id, after=0):
    body = client.get(f"/api/jobs/{job_id}/events", params={"after": after}).text
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_search_result_survives_restart(client, monkeypatch):
    def fake_find_deals(origin, destination, dates_hint, *args, on_event=None, **kw):
        on_event({"kind": "phase", "text": "Researching…"})
        return [FlightDeal(origin=origin, destination=destination, airline="Wizz Air", price=120,
                           currency="EUR", depart_date="2026-11-10", return_date="")]

    monkeypatch.setattr(agent, "find_deals", fake_find_deals)
    body = {"origin": "TLV", "destination": "Rome", "dates_hint": "November", "trip_type": "one_way"}
    job = finished(client, client.post("/api/search", json=body).json()["job_id"])
    assert job["status"] == "done" and job["created_at"] and job["updated_at"]

    jobs.reset_state()  # restart
    again = client.get(f"/api/jobs/{job['job_id']}").json()
    assert again["status"] == "done" and again["query"] == job["query"]
    assert [d["airline"] for d in again["deals"]] == ["Wizz Air"]
    assert again["deals"][0]["booking_links"] == job["deals"][0]["booking_links"]
    # The stored request re-submits as-is ("Run again").
    assert again["request"]["origin"] == "TLV" and again["request"]["trip_type"] == "one_way"
    rerun = client.post("/api/search", json=again["request"])
    assert rerun.status_code == 202 and finished(client, rerun.json()["job_id"])["status"] == "done"


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/events").status_code == 404
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_running_job_is_stored_with_events_while_it_runs(client, monkeypatch):
    monkeypatch.setattr(jobs, "FLUSH_AT_EVENTS", 3)
    release = threading.Event()

    def emit(job_id):
        for i in range(3):
            jobs.emit_event(job_id, {"kind": "phase", "text": f"step {i}"})

    job = blocked_job(release, emit)
    try:
        # the flusher writes the batch before the job ends
        stored = wait_until(lambda: storage.get_job_events(job.job_id))
        assert [e["text"] for e in stored] == ["step 0", "step 1", "step 2"]
        row = storage.get_job(job.job_id)
        assert row["status"] == "running" and json.loads(row["request"]) == {"origin": "TLV"}
    finally:
        release.set()
    assert finished(client, job.job_id)["summary"] == {"ok": True}
    assert storage.get_job(job.job_id)["status"] == "done"


def test_events_are_batched_and_deltas_coalesced(client, monkeypatch):
    writes = []
    real_append = storage.append_job_events
    monkeypatch.setattr(storage, "append_job_events",
                        lambda job_id, seq, events: (writes.append(len(events)), real_append(job_id, seq, events)))

    def work(job_id):
        jobs.emit_event(job_id, {"kind": "phase", "text": "Researching…"})
        for i in range(500):
            jobs.emit_event(job_id, {"kind": "delta", "text": f"{i} "})
        jobs.emit_event(job_id, {"kind": "search", "text": "cheap flights TLV Rome"})
        jobs.emit_event(job_id, {"kind": "delta", "text": "done."})
        return {}

    job = jobs.start_job("search", "q", work)
    finished(client, job.job_id)
    assert len(writes) <= 2  # not one write per token
    stored = storage.get_job_events(job.job_id)
    assert [e["kind"] for e in stored] == ["phase", "delta", "search", "delta"]
    assert stored[1]["text"] == "".join(f"{i} " for i in range(500))
    # In memory the live stream still has every raw event.
    assert len(sse_events(client, job.job_id)) == 503 + 1


def test_stored_events_are_capped(client, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_STORED_EVENTS", 5)

    def work(job_id):
        for i in range(20):
            jobs.emit_event(job_id, {"kind": "search", "text": str(i)})
        return {}

    job = jobs.start_job("search", "q", work)
    finished(client, job.job_id)
    assert [e["text"] for e in storage.get_job_events(job.job_id)] == ["0", "1", "2", "3", "4"]


def test_sse_replays_from_db_after_restart(client):
    def work(job_id):
        jobs.emit_event(job_id, {"kind": "phase", "text": "Researching…"})
        jobs.emit_event(job_id, {"kind": "search", "text": "flights to Rome"})
        return {}

    job = jobs.start_job("search", "q", work)
    finished(client, job.job_id)
    live = sse_events(client, job.job_id)
    jobs.reset_state()
    assert sse_events(client, job.job_id) == live == [
        {"kind": "phase", "text": "Researching…"},
        {"kind": "search", "text": "flights to Rome"},
        {"kind": "end", "text": "done"},
    ]
    assert sse_events(client, job.job_id, after=1)[0]["text"] == "flights to Rome"


def test_startup_marks_dead_processes_jobs_interrupted(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    jobs.reset_state()
    # What a process killed mid-run leaves behind: "running" rows and some events.
    for job_id, kind in (("s1", "search"), ("w1", "watch_run")):
        storage.save_job({"job_id": job_id, "kind": kind, "status": "running", "query": "q",
                          "request": json.dumps({"origin": "TLV"}) if kind == "search" else
                          json.dumps({"watch_id": 3}),
                          "created_at": "2026-10-01T05:00:00+00:00", "updated_at": "2026-10-01T05:00:00+00:00"})
    storage.append_job_events("s1", 0, [{"kind": "phase", "text": "Researching…"}])
    storage.save_job({"job_id": "old", "kind": "search", "status": "done", "query": "q",
                      "created_at": "2026-08-01T00:00:00+00:00", "updated_at": "2026-08-01T00:00:00+00:00"})

    with TestClient(main.app) as client:  # runs the lifespan startup
        search = client.get("/api/jobs/s1").json()
        watch = client.get("/api/jobs/w1").json()
        assert search["status"] == "interrupted" and "restarted" in search["error"]
        assert "won't re-run" in search["error"] and search["request"] == {"origin": "TLV"}
        assert watch["status"] == "interrupted" and "watch run" in watch["error"]
        assert watch["request"] == {"watch_id": 3}
        assert sse_events(client, "s1") == [{"kind": "phase", "text": "Researching…"},
                                             {"kind": "end", "text": "interrupted"}]
        assert client.get("/api/jobs/old").status_code == 404  # trimmed after 30 days
        # cancelling a job this process doesn't run is a no-op, not an error
        assert client.post("/api/jobs/s1/cancel").json()["status"] == "interrupted"
    jobs.reset_state()


def test_shutdown_marks_running_jobs_interrupted(client):
    release = threading.Event()
    job = blocked_job(release, kind="watch_run")
    try:
        wait_until(lambda: storage.get_job(job.job_id))
        jobs.shutdown()
        row = storage.get_job(job.job_id)
        assert row["status"] == "interrupted" and row["error"] == jobs.WATCH_INTERRUPTED
    finally:
        release.set()
    finished(client, job.job_id)


def test_cancel_still_works_and_is_stored(client):
    def work(job_id):
        for _ in range(500):
            jobs.emit_or_cancel(job_id, {"kind": "delta", "text": "."})
            time.sleep(0.01)
        return {}

    job = jobs.start_job("search", "q", work)
    assert client.post(f"/api/jobs/{job.job_id}/cancel").json()["status"] == "running"
    assert finished(client, job.job_id)["status"] == "cancelled"
    assert storage.get_job(job.job_id)["status"] == "cancelled"
    jobs.reset_state()
    assert client.get(f"/api/jobs/{job.job_id}").json()["status"] == "cancelled"


def test_db_outage_never_breaks_a_job(client, monkeypatch):
    def down(*args, **kwargs):
        raise RuntimeError("Turso unreachable")

    for name in ("save_job", "append_job_events"):
        monkeypatch.setattr(storage, name, down)

    def work(job_id):
        jobs.emit_event(job_id, {"kind": "phase", "text": "x"})
        return {"summary": {"ok": 1}}

    job = jobs.start_job("watch_run", "q", work)
    assert finished(client, job.job_id)["summary"] == {"ok": 1}
