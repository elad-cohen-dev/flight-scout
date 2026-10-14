"""Adaptive watches (app/cadence.py): cadence by days to departure and price
volatility, the monthly budget per watch, and how the plan meets the pre-screen,
the daily sweep, the digest and the API. Provider HTTP is faked."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import alerts, cadence, engine, main, storage, watcher
from app.models import WatchCreate, WatchPlan
from tests.test_explore import LONDON, NOW, http, web  # noqa: F401 — fixtures

TODAY = NOW.date()  # 2026-10-01


# --- cadence -------------------------------------------------------------------------

@pytest.mark.parametrize("days_out, interval", [(200, 7), (91, 7), (90, 3), (30, 3), (29, 1), (0, 1), (None, 3)])
def test_base_interval_by_days_to_departure(days_out, interval):
    assert cadence.base_interval(days_out)[0] == interval


@pytest.mark.parametrize("prices, word", [
    ([], ""), ([300], ""),
    ([300, 301, 302], "flat"), ([300, 302], ""),             # flat needs 3 runs
    ([300, 335], "moving"),                                  # one 10%+ step
    ([300, 320, 350], "moving"),                             # a 15% range
    ([300, 310, 305, 315], ""),                              # ordinary moves
    ([100, 300, 301, 302, 303, 304], "flat"),                # only the last 5 count
])
def test_volatility(prices, word):
    assert cadence.volatility(prices) == word


def test_interval_slows_on_flat_prices_and_speeds_up_on_moves():
    assert cadence.interval_for(120, [300, 301, 300]) == (14, "120 days out, prices steady")
    assert cadence.interval_for(60, [300, 301, 300])[0] == 6
    assert cadence.interval_for(10, [300, 301, 300])[0] == 2
    assert cadence.interval_for(120, [300, 350]) == (3, "120 days out, prices moving")
    assert cadence.interval_for(60, [300, 350])[0] == 1
    assert cadence.interval_for(10, [300, 350])[0] == 1


def test_fair_shares():
    assert cadence.fair_shares({1: None, 2: None}, 230) == {1: 80, 2: 80}  # 70% of 230 = 161, split
    assert cadence.fair_shares({1: 100, 2: None, 3: None}, 230) == {1: 100, 2: 30, 3: 30}
    assert cadence.fair_shares({1: 500, 2: None}, 230) == {1: 500, 2: 0}
    assert cadence.fair_shares({}, 230) == {}


def plan(per_check=1, days_out=60, prices=(), used=0, budget=30, last=None, today=date(2026, 10, 10)):
    return cadence.make_plan(per_check, days_out, list(prices), used, budget, last, today)


def test_first_check_is_due_today():
    p = plan()
    assert (p.interval_days, p.next_check, p.next_in_days) == (3, date(2026, 10, 10), 0)
    assert p.text == "next live check: today · 0/30 credits this month"
    assert p.reason == "60 days out: every 3 days"
    assert cadence.due(p, date(2026, 10, 10))


def test_next_check_follows_the_last_one():
    p = plan(last=date(2026, 10, 8), used=4)
    assert (p.next_check, p.next_in_days) == (date(2026, 10, 11), 1)
    assert p.text == "next live check: tomorrow · 4/30 credits this month"
    assert not cadence.due(p, date(2026, 10, 10)) and cadence.due(p, date(2026, 10, 11))
    assert plan(last=date(2026, 10, 1)).next_check == date(2026, 10, 10)  # overdue = today
    assert plan(per_check=3, last=date(2026, 10, 8), budget=60).text.startswith("next live check: tomorrow")
    assert "in 2 days" in plan(days_out=200, last=date(2026, 10, 5)).text


def test_interval_stretches_to_fit_the_budget():
    # 3 credits a check, 9 left, 22 days left in October: 3 checks -> every 8 days.
    p = plan(per_check=3, days_out=10, used=21, budget=30)
    assert p.interval_days == 8 and "every 8 days to fit the budget" in p.reason
    assert p.per_month == 30  # never projected over the budget


def test_budget_used_waits_for_next_month():
    p = plan(per_check=3, used=29, budget=30, last=date(2026, 10, 9))
    assert p.next_check == date(2026, 11, 1) and p.next_in_days == 22
    assert p.text == "next live check: in 22 days (budget used) · 29/30 credits this month"
    assert not cadence.due(p, date(2026, 10, 10)) and not cadence.has_budget(p)
    zero = plan(budget=0)
    assert not cadence.due(zero, date(2026, 10, 10)) and zero.next_check == date(2026, 11, 1)


def test_projection_counts_the_checks_still_to_come():
    p = plan(per_check=2, days_out=200, used=4, budget=100, last=date(2026, 10, 8))
    # next 15th, then 22nd, 29th: 3 more checks of 2 in October.
    assert (p.next_check, p.per_month) == (date(2026, 10, 15), 10)


def test_no_live_searches_no_plan():
    p = plan(per_check=0)
    assert p.per_check == 0 and p.text == "" and cadence.due(p, date(2026, 10, 10))
    assert cadence.due(None, date(2026, 10, 10)) and cadence.has_budget(None)


# --- storage: the credit log ------------------------------------------------------------

def test_credit_log_sums_this_month_and_keeps_the_last_check(temp_db):
    storage.record_watch_credits(1, 3, NOW - timedelta(days=5))       # September
    storage.record_watch_credits(1, 2, NOW + timedelta(days=1))
    storage.record_watch_credits(1, 1, NOW + timedelta(days=2))
    storage.record_watch_credits(2, 0, NOW)                           # nothing spent: no row
    storage.log_prescreen(3, True, "live", 300, "USD", NOW - timedelta(days=40))
    usage = storage.watch_credit_usage(NOW + timedelta(days=3))
    assert usage[1] == (3, NOW + timedelta(days=2))
    assert 2 not in usage
    assert usage[3] == (0, NOW - timedelta(days=40))  # the pre-screen log predates the credit log


def test_credit_budget_is_stored_and_patchable(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    w = storage.add_watch(WatchCreate(**LONDON, max_price=450, credit_budget=12))
    assert storage.get_watch(w.id).credit_budget == 12
    client = TestClient(main.app)
    body = client.patch(f"/api/watches/{w.id}", json={"credit_budget": None}).json()
    assert body["credit_budget"] is None
    assert client.patch(f"/api/watches/{w.id}", json={"credit_budget": -1}).status_code == 422
    # A budget change isn't a new search: the last run stays.
    storage.update_watch_run(w.id, 400)
    client.patch(f"/api/watches/{w.id}", json={"credit_budget": 20})
    assert storage.get_watch(w.id).last_best_price == 400


# --- engine: plans per watch -------------------------------------------------------------

def london(**kw):
    return storage.add_watch(WatchCreate(**{**LONDON, "max_price": 450, **kw}))


def rows_at(*items):
    """(days ago, price, depart_date) -> verified sighting rows."""
    return [{"found_at": (NOW - timedelta(days=d)).isoformat(), "price": p, "currency": "USD",
             "search_params": None, "source": "google_flights", "depart_date": dep} for d, p, dep in items]


def test_watch_plans_share_the_budget(temp_db, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "100")
    a, b = london(), london(destination="Paris")
    c = london(credit_budget=10)
    region = london(destination="Greece")  # web agent: no credits, no plan
    plans = engine.watch_plans(storage.list_watches(), {}, NOW)
    assert set(plans) == {a.id, b.id, c.id}
    assert (plans[a.id].budget, plans[b.id].budget, plans[c.id].budget) == (30, 30, 10)
    assert plans[a.id].budget_default and not plans[c.id].budget_default
    assert region.id not in plans


def test_watch_plan_uses_the_latest_fare_and_its_moves(temp_db, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    w = london()
    sightings = {w.id: rows_at((3, 400, "2026-11-20"), (2, 401, "2026-11-20"), (1, 402, "2026-11-25"))}
    storage.record_watch_credits(w.id, 3, NOW - timedelta(days=1))  # September's credits
    p = engine.watch_plans([w], sightings, NOW)[w.id]
    # 55 days to the latest best's departure, flat prices: every 6 days.
    assert p.interval_days == 6 and p.reason == "55 days out, prices steady: every 6 days"
    assert (p.used, p.next_check) == (0, date(2026, 10, 6))  # a new month's budget, the same cadence
    assert p.text == f"next live check: in 5 days · 0/{p.budget} credits this month"
    assert engine.watch_depart(w, [], TODAY) == date(2026, 11, 5)  # no runs: the middle of the window


def test_prescreen_decision_with_a_plan(temp_db):
    w = london(max_price=100)
    due = plan(today=TODAY)
    later = plan(today=TODAY, last=TODAY - timedelta(days=1), used=3)
    spent = plan(today=TODAY, used=30)
    decide = engine.prescreen_decision
    live, why = decide(w, 400, [], NOW, 10, NOW, plan=due)
    assert live and why == "plan: 60 days out: every 3 days"
    live, why = decide(w, 400, [], NOW - timedelta(days=30), 10, NOW, plan=later)  # the plan replaces "weekly"
    assert not live and why == "cached best $400 is not close to firing; plan: in 2 days · 3/30 credits this month"
    assert decide(w, 105, [], NOW, 10, NOW, plan=later)[0] is True  # near the threshold: live anyway
    assert decide(w, 105, [], NOW, 10, NOW, plan=spent) == (False, "this watch's monthly budget is used (30/30 credits)")
    # The Explore path keeps the old rules for the confirmation (the plan gated the pre-screen).
    live, why = decide(w, 400, [], NOW - timedelta(days=2), 10, NOW, plan=due, plan_cadence=False)
    assert not live and "next live check in 5 days" in why


# --- the sweep ---------------------------------------------------------------------------

def test_sweep_skips_a_watch_that_is_not_due_and_says_so(http, web, monkeypatch):
    sent = []
    monkeypatch.setattr(watcher, "send_notification", lambda title, plain, *a, **kw: sent.append(plain) or ["ntfy"])
    w = london(max_price=100)
    storage.update_watch_run(w.id, 389)
    storage.record_watch_credits(w.id, 3, datetime.now(timezone.utc) - timedelta(hours=1))
    summary = watcher.run_watches()
    [row] = summary["results"]
    assert row["skipped"] and row["prescreen"].startswith("skipped: plan: ") and http == []
    [digest] = sent
    assert f"TLV → London · #{w.id}: skipped today · last $389 · next live check " in digest
    assert storage.get_watch(w.id).last_best_price == 389  # nothing overwritten


def test_run_now_ignores_the_plan_and_counts_its_credits(http, web, monkeypatch):
    monkeypatch.setattr(watcher, "send_notification", lambda *a, **kw: [])
    w = london()
    storage.record_watch_credits(w.id, 3, datetime.now(timezone.utc) - timedelta(hours=1))
    summary = watcher.run_watches(only_id=w.id, notify=False)
    [row] = summary["results"]
    assert not row.get("skipped") and http.engines()[0] == "google_travel_explore"
    used, _ = storage.watch_credit_usage()[w.id]
    assert used == 3 + len(http)


def test_watches_api_carries_the_plan(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    london()
    paused = london(destination="Paris")
    storage.update_watch(paused.id, WatchCreate(**{**LONDON, "destination": "Paris", "max_price": 450}), False)
    client = TestClient(main.app)
    active, stopped = client.get("/api/watches").json()
    plan_ = WatchPlan.model_validate(active["plan"])
    assert plan_.per_check == 3 and plan_.next_in_days == 0 and plan_.text.startswith("next live check: today")
    assert plan_.budget == int(230 * cadence.WATCH_POOL_SHARE)  # the only active watch
    assert stopped["plan"] is None


def test_digest_lines_show_the_advice(temp_db):
    from app.models import Advice, StoredDeal, Watch
    w = Watch(id=7, origin="TLV", destination="London", max_price=300, currency="USD",
              created_at=NOW, dates_hint="November")
    deal = StoredDeal(id=1, search_query="q", found_at=NOW, origin="TLV", destination="LTN", airline="Wizz Air",
                      price=280, currency="USD", depart_date="2026-11-17", return_date="2026-11-24")
    buy = Advice(verdict="buy_now", label="Buy now", reason="18% under Google's typical $340–420, 20 days out")
    hit = alerts.WatchResult(watch=w, best_price=280, best=deal, advice=buy,
                             hits=[alerts.RuleHit("threshold", "At or under your $300 target")])
    quiet = alerts.WatchResult(watch=w, best_price=320, best=deal,
                               advice=Advice(verdict="wait", label="Wait", reason="r"))
    unknown = alerts.WatchResult(watch=w, best_price=320, best=deal,
                                 advice=Advice(verdict="not_enough_data", label="Not enough data", reason="r"))
    _, plain, rich = alerts.format_digest([hit, quiet, unknown], NOW)
    assert "🛒 Buy now — 18% under Google's typical $340–420, 20 days out" in plain
    assert "🛒 <b>Buy now</b> — 18% under" in rich
    assert "TLV → London · #7: $320 · Wait" in plain
    assert "Not enough data" not in plain  # no verdict, no noise
