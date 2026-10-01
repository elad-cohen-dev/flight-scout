"""scripts/run_watches.py — the daily sweep run directly (GitHub Actions), no web
server. The engine and the notifier are faked: no network, no LLM, no Telegram."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app import engine, jobs, storage, watcher
from app.models import FlightDeal, Passengers, SearchMeta, WatchCreate

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_watches.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_watches_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_watches_script"] = module
    spec.loader.exec_module(module)
    return module


script = _load()


@pytest.fixture
def env(temp_db, monkeypatch, tmp_path):
    """Temp DB, a configured (fake) Telegram channel, a step-summary file, and a
    notifier that records digests instead of sending them."""
    for var in ("NTFY_TOPIC", "GITHUB_RUN_ID", "GITHUB_REPOSITORY", "GITHUB_ACTIONS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fake-telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    sent = []

    def fake_send(title, message, click_url="", html=None, quiet=False):
        sent.append({"title": title, "message": message, "quiet": quiet})
        return ["telegram"]

    monkeypatch.setattr(watcher, "send_notification", fake_send)
    jobs.reset_state()
    yield {"sent": sent, "summary": summary_file, "monkeypatch": monkeypatch}
    jobs.reset_state()


def fake_engine(monkeypatch, price=250, fail_for=()):
    """find_for_watch returning one deal at `price`; raises for destinations in `fail_for`."""
    def find_for_watch(watch, params, prior_rows, on_event=None, now=None):
        if watch.destination in fail_for:
            raise RuntimeError(f"provider down for {watch.destination}")
        on_event({"kind": "phase", "text": f"Searching {watch.destination}…"})
        deal = FlightDeal(origin=watch.origin, destination=watch.destination, airline="Wizz Air", price=price,
                          currency="USD", depart_date="2026-11-10", return_date="2026-11-14")
        meta = SearchMeta(sources=["web"], decision="no pre-screen (test)")
        return engine.SearchOutcome([deal], meta)

    monkeypatch.setattr(engine, "find_for_watch", find_for_watch)


def make_watch(destination="Athens", max_price=300):
    return storage.add_watch(WatchCreate(origin="TLV", destination=destination, dates_hint="November",
                                         max_price=max_price, currency="USD", max_stops=0,
                                         passengers=Passengers(adults=1)))


def stored_jobs():
    return storage._with_conn(lambda conn: storage._rows_as_dicts(conn.execute("SELECT * FROM jobs")))


def test_sweep_records_job_sends_digest_and_exits_0(env, capsys):
    fake_engine(env["monkeypatch"])
    w = make_watch()

    assert script.main([]) == 0

    [row] = stored_jobs()
    assert (row["kind"], row["status"], row["query"]) == ("watch_run", "done", "scheduled watch run")
    assert json.loads(row["request"]) == {"scheduled": True, "runner": "script"}
    summary = json.loads(row["result"])["summary"]
    assert summary["watches_run"] == 1 and summary["digest_channels"] == ["telegram"]
    [result] = summary["results"]
    assert result["watch_id"] == w.id and result["best_price"] == 250 and result["alerted"]
    # Progress events were stored too, so the app can replay the run.
    assert any("Searching Athens" in e.get("text", "") for e in storage.get_job_events(row["job_id"]))
    # One digest, logged in the anti-spam table like the API's sweep does.
    assert len(env["sent"]) == 1 and not env["sent"][0]["quiet"]
    assert [a.watch_id for a in storage.list_alerts()] == [w.id]
    assert storage.get_watch(w.id).last_best_price == 250

    md = env["summary"].read_text()
    assert "### Watch sweep: done" in md and "TLV → Athens" in md and "ALERT" in md
    assert row["job_id"] in capsys.readouterr().out


def test_actions_request_links_the_run(env):
    fake_engine(env["monkeypatch"])
    make_watch()
    mp = env["monkeypatch"]
    mp.setenv("GITHUB_ACTIONS", "true")
    mp.setenv("GITHUB_RUN_ID", "99")
    mp.setenv("GITHUB_REPOSITORY", "me/flight-scout")

    assert script.main([]) == 0
    [row] = stored_jobs()
    assert json.loads(row["request"]) == {"scheduled": True, "runner": "github-actions",
                                          "run_url": "https://github.com/me/flight-scout/actions/runs/99"}


def test_no_notify_sends_nothing(env):
    fake_engine(env["monkeypatch"])
    make_watch()
    assert script.main(["--no-notify"]) == 0
    assert env["sent"] == [] and storage.list_alerts() == []


def test_partial_failure_warns_unless_strict(env, capsys):
    fake_engine(env["monkeypatch"], fail_for={"Rome"})
    make_watch("Athens")
    make_watch("Rome")

    assert script.main([]) == 0
    assert "warning: watch #2 (TLV → Rome) failed: provider down for Rome" in capsys.readouterr().err
    assert script.main(["--strict"]) == 1


def test_every_watch_failing_exits_1(env, capsys):
    fake_engine(env["monkeypatch"], fail_for={"Athens"})
    make_watch()
    assert script.main([]) == 1
    assert "error: watch #1" in capsys.readouterr().err
    assert "failed: provider down for Athens" in env["summary"].read_text()


def test_undelivered_digest_exits_1(env):
    fake_engine(env["monkeypatch"])
    make_watch()
    env["monkeypatch"].setattr(watcher, "send_notification", lambda *a, **kw: [])
    assert script.main([]) == 1


def test_no_channel_configured_is_not_a_failure(env):
    fake_engine(env["monkeypatch"])
    make_watch()
    env["monkeypatch"].delenv("TELEGRAM_BOT_TOKEN")
    env["monkeypatch"].setattr(watcher, "send_notification", lambda *a, **kw: [])
    assert script.main([]) == 0
    assert "none configured" in env["summary"].read_text()


def test_run_error_records_error_job_and_exits_1(env):
    def broken(*a, **kw):
        raise RuntimeError("database unreachable")

    env["monkeypatch"].setattr(storage, "list_watches", broken)
    assert script.main([]) == 1
    [row] = stored_jobs()
    assert (row["status"], row["error"]) == ("error", "database unreachable")


def test_interrupt_records_interrupted_job(env):
    def interrupted(*a, **kw):
        raise KeyboardInterrupt

    env["monkeypatch"].setattr(engine, "find_for_watch", interrupted)
    make_watch()
    assert script.main([]) == 130
    [row] = stored_jobs()
    assert row["status"] == "interrupted" and row["error"] == script.INTERRUPTED
    assert jobs.get_job(row["job_id"]).status == "interrupted"


def test_secrets_never_reach_the_summary(env, capsys):
    fake_engine(env["monkeypatch"], fail_for={"Athens"})
    env["monkeypatch"].setenv("SERPAPI_API_KEY", "Athens")  # the failure message now contains a "secret"
    make_watch()
    script.main([])
    md = env["summary"].read_text()
    out = capsys.readouterr()
    assert "provider down for ***" in md
    assert "down for Athens" not in md + out.out + out.err


def test_run_job_without_a_server(temp_db):
    """jobs.run_job: same rows as start_job, in the calling thread."""
    jobs.reset_state()

    def work(job_id):
        jobs.emit_event(job_id, {"kind": "phase", "text": "hello"})
        return {"summary": {"ok": True}}

    job = jobs.run_job("watch_run", "q", work, request={"scheduled": True})
    assert job.status == "done" and job.summary == {"ok": True}
    jobs.reset_state()  # a "restart": only the DB is left
    again = jobs.get_job(job.job_id)
    assert again.status == "done" and again.summary == {"ok": True} and again.request == {"scheduled": True}
    assert storage.get_job_events(job.job_id) == [{"kind": "phase", "text": "hello"}]


def test_server_restart_leaves_a_live_actions_sweep_alone(temp_db):
    """Render waking mid-sweep must not mark the Actions job interrupted — unless
    it is older than any run could be (its runner died without a final write)."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    def row(job_id, request, age_minutes):
        at = (now - timedelta(minutes=age_minutes)).isoformat()
        storage.save_job({"job_id": job_id, "kind": "watch_run", "status": "running", "query": "q",
                          "request": json.dumps(request) if request else None, "result": None,
                          "error": None, "created_at": at, "updated_at": at})

    row("actions-live", {"scheduled": True, "runner": "github-actions"}, 5)
    row("actions-dead", {"scheduled": True, "runner": "github-actions"}, 31)
    row("server", {"scheduled": True}, 5)
    row("legacy", None, 5)

    assert sorted(jobs.recover_interrupted()) == ["actions-dead", "legacy", "server"]
    assert storage.get_job("actions-live")["status"] == "running"
    assert storage.get_job("actions-dead")["status"] == "interrupted"
