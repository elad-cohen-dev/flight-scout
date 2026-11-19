"""Dates that have passed (3.1.1). A search, or a watch, whose last possible
departure is behind us is not searched: no fare API, no web agent, no credits.
It says so instead. Before this the window was clamped to "today" and searched
anyway — a paid search for a trip nobody asked for, every day for a watch.

Provider HTTP and the LLM agent are faked (tests/test_engine.py fixtures); time
passes by moving the suite's frozen clock (tests/conftest.py)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import alerts, cadence, engine, holidays, main, storage, telegram_bot, watcher
from app.holidays import HolidayWindow
from app.models import Passengers, SearchParams, WatchCreate
from tests.conftest import NOW, TODAY
from tests.test_engine import http, keys, web  # noqa: F401 — fixtures

TWO = SearchParams(passengers=Passengers(adults=2))
PAST = "2026-09-05 to 2026-09-09"   # flown before the suite's today, 2026-10-01
SOON = "2026-10-05 to 2026-10-09"   # still ahead of it...
LATER = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)  # ...and behind this


# --- the date plan -----------------------------------------------------------------------

@pytest.mark.parametrize("hint, params, expired", [
    (PAST, {}, True),                                            # an exact pair
    ("2026-09-30", {"trip_type": "one_way"}, True),              # yesterday
    ("2026-10-01", {"trip_type": "one_way"}, False),             # today still flies
    (SOON, {}, False),
    ("September 2026", {}, True),                                # a month, with its year
    ("September", {}, False),                                    # without one: the next September
    ("from 2026-09-03 to 2026-09-20", {}, True),                 # a stated window
    ("", {"depart_from": date(2026, 9, 1), "depart_to": date(2026, 9, 30)}, True),    # the form's window
    ("", {"depart_from": date(2026, 9, 1), "depart_to": date(2026, 10, 30)}, False),  # half gone: from today
    ("after 2026-09-03", {}, False),                             # no stated end
    ("", {}, False),
    ("sometime in winter", {}, False),
])
def test_plan_knows_when_the_dates_have_passed(hint, params, expired):
    plan = engine.plan_dates(hint, SearchParams(**params), TODAY)
    assert plan.expired is expired
    assert plan.start >= TODAY and plan.end >= plan.start  # the window itself never reaches back
    if expired:
        assert plan.pairs == []


def test_a_holiday_expires_with_its_last_departure_day(monkeypatch):
    def window(start: date, end: date) -> SearchParams:
        w = HolidayWindow(id="test-break", name="Test break", kind="sukkot", school_year="2026-27", start=start,
                          end=end, nights_min=2, nights_max=4, depart_from=start,
                          depart_to=date.fromordinal(end.toordinal() - 2), sources=["https://example.org"])
        monkeypatch.setattr(holidays, "windows", lambda: (w,))
        return SearchParams(holiday=w.id)

    assert engine.plan_dates("x", window(date(2026, 9, 10), date(2026, 9, 14)), TODAY).expired
    # Its last day to fly out (two nights before the end) is today: still on.
    assert not engine.plan_dates("x", window(date(2026, 9, 25), date(2026, 10, 3)), TODAY).expired
    assert engine.plan_dates("x", window(date(2026, 9, 25), date(2026, 10, 2)), TODAY).expired


# --- searches ----------------------------------------------------------------------------

@pytest.mark.parametrize("serp, tp", [(True, True), (True, False), (False, True), (False, False)])
@pytest.mark.parametrize("destination, extra", [("Prague", {}), ("Greece", {}), ("Prague", {"nearby_airports": True})])
def test_search_for_past_dates_spends_nothing(http, web, monkeypatch, serp, tp, destination, extra):
    """Whatever is configured and wherever the request would have been routed —
    live fares, the cached calendar, Explore or the web agent."""
    keys(monkeypatch, serp=serp, tp=tp)
    out = engine.search("TLV", destination, PAST, TWO.model_copy(update=extra))
    assert out.deals == [] and http == [] and web == []
    assert out.meta.dates_passed and out.meta.live_searches == 0 and out.meta.sources == []
    assert out.meta.messages == [engine.DATES_PASSED]


def test_the_same_search_for_dates_ahead_still_runs(http, web, monkeypatch):
    keys(monkeypatch)
    out = engine.search("TLV", "Prague", "2026-11-05 to 2026-11-09", TWO)
    assert out.deals and http and not out.meta.dates_passed


def test_search_job_for_past_dates_says_so_and_stores_nothing(http, web, monkeypatch):
    import time

    keys(monkeypatch)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    client = TestClient(main.app)
    job = client.post("/api/search", json={"origin": "TLV", "destination": "Prague", "dates_hint": PAST,
                                           "passengers": {"adults": 2}}).json()
    for _ in range(200):
        job = client.get(f"/api/jobs/{job['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "done", job
    assert job["deals"] == [] and job["meta"]["dates_passed"] is True
    assert job["meta"]["messages"] == [engine.DATES_PASSED]
    assert http == [] and web == [] and client.get("/api/deals").json() == []


# --- watches -----------------------------------------------------------------------------

def prague_watch(dates: str = SOON, **kw):
    return storage.add_watch(WatchCreate(**{"origin": "TLV", "destination": "Prague", "dates_hint": dates,
                                            "max_price": 900, "currency": "USD", **kw}))


def test_a_watch_stops_costing_once_its_dates_pass(http, web, monkeypatch, frozen_clock):
    keys(monkeypatch, tp=False)  # as in production: SerpApi only
    monkeypatch.setattr(watcher, "send_notification", lambda *a, **kw: [])
    w = prague_watch()
    before = engine.watch_credits(w)
    assert (before.per_check, before.dates_passed) == (1, False)
    assert engine.watch_plans([w], {})[w.id].per_check == 1

    frozen_clock.move_to(LATER)
    after = engine.watch_credits(w)
    assert (after.per_check, after.per_month, after.dates_passed) == (0, 0, True)
    assert "dates have passed" in after.text
    assert engine.watch_plans([w], {}) == {}  # nothing to plan: no "0 days out: daily"

    [row] = watcher.run_watches(notify=False)["results"]  # the daily sweep
    assert row["expired"] and row["skipped"] and row["best_price"] is None and row["deals_found"] == 0
    assert row["prescreen"] == "skipped: its dates have passed"
    [again] = watcher.run_watches(only_id=w.id, notify=False)["results"]  # "Run now" has nothing to find either
    assert again["expired"]
    assert http == [] and web == []
    assert storage.watch_credit_usage().get(w.id, (0, None))[0] == 0
    assert storage.list_deals() == [] and storage.get_watch(w.id).last_best_price is None


def test_an_expired_watch_gives_its_budget_share_back(temp_db, monkeypatch, frozen_clock):
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    gone, live = prague_watch(), prague_watch("2026-11-05 to 2026-11-09")
    pool = int(230 * cadence.WATCH_POOL_SHARE)
    plans = engine.watch_plans([gone, live], {})
    assert plans[gone.id].budget == plans[live.id].budget == pool // 2
    frozen_clock.move_to(LATER)
    plans = engine.watch_plans([gone, live], {})
    assert list(plans) == [live.id] and plans[live.id].budget == pool
    assert engine.watch_projection([gone, live], LATER.date(), plans)[0] == plans[live.id].per_month


def test_the_digest_and_the_api_say_so(http, web, monkeypatch, frozen_clock):
    keys(monkeypatch, tp=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    sent = []
    monkeypatch.setattr(watcher, "send_notification", lambda title, plain, *a, **kw: sent.append(plain) or ["ntfy"])
    w = prague_watch()
    frozen_clock.move_to(LATER)

    summary = watcher.run_watches()
    assert summary["alerts_sent"] == 0 and http == [] and web == []
    [digest] = sent
    assert f"TLV → Prague · #{w.id}: its dates have passed — no longer checked" in digest

    client = TestClient(main.app)
    [watch] = client.get("/api/watches").json()
    assert watch["credits"]["dates_passed"] is True and watch["plan"] is None
    usage = client.get("/api/usage").json()
    assert usage["watch_projection"] == 0 and usage["warning"] == ""
    # Pausing it — or giving it new dates — still works, and new dates revive it.
    assert client.patch(f"/api/watches/{w.id}", json={"dates_hint": "2026-11-05 to 2026-11-09"}).status_code == 200
    [watch] = client.get("/api/watches").json()
    assert watch["credits"]["dates_passed"] is False and watch["credits"]["per_check"] == 1


def test_digest_line_for_an_expired_watch_is_plain_and_escaped():
    from app.models import Watch

    w = Watch(id=7, origin="TLV", destination="A & B", max_price=300, currency="USD", created_at=NOW,
              dates_hint=PAST, last_best_price=250)
    _, plain, rich = alerts.format_digest([alerts.WatchResult(watch=w, expired=True)], NOW)
    assert "TLV → A & B · #7: its dates have passed" in plain and "$250" not in plain
    assert "TLV → A &amp; B · #7: its dates have passed" in rich


def test_the_bot_marks_expired_watches(temp_db, frozen_clock):
    w = prague_watch()
    assert "dates passed" not in telegram_bot.format_watches(storage.list_watches(), NOW)
    frozen_clock.move_to(LATER)
    assert f"<b>#{w.id}</b> TLV → Prague · ≤ $900 · ⌛ dates passed" in telegram_bot.format_watches(
        storage.list_watches(), LATER)
    reply = telegram_bot.format_run_result(w.id, {"results": [{"watch_id": w.id, "expired": True, "skipped": True,
                                                               "best_price": None}]}, LATER)
    assert "its dates have passed" in reply and f"/pause {w.id}" in reply
    storage.update_watch(w.id, WatchCreate(origin="TLV", destination="Prague", dates_hint=SOON, max_price=900), False)
    assert "dates passed" not in telegram_bot.format_watches(storage.list_watches(), LATER)  # paused wins
