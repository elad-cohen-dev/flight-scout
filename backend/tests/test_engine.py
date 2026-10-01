"""Search orchestration (app/engine.py): which source answers which request,
budget/cache behaviour, the usage endpoint, and the watch pre-screen. Provider
HTTP is faked with fixture responses; the LLM agent is faked too."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent, engine, main, storage, watcher
from app.agent import SearchCancelled
from app.models import AlertSettings, FlightDeal, Passengers, SearchParams, WatchCreate
from app.providers import base

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
TWO = SearchParams(passengers=Passengers(adults=2))


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def http(temp_db, monkeypatch):
    """Fixture-backed provider HTTP; records (provider, params) per call."""
    calls: list[tuple[str, dict]] = []

    def fake(url, params, headers=None, *, provider):
        calls.append((provider, dict(params)))
        if provider == "serpapi":
            if "departure_token" in params:
                return load("serpapi_return_flights.json")
            return load("serpapi_round_trip.json" if params.get("type") == "1" else "serpapi_one_way.json")
        return load("travelpayouts_calendar.json" if "destination" in params else "travelpayouts_explore.json")

    monkeypatch.setattr(base, "http_get_json", fake)
    return calls


@pytest.fixture
def web(monkeypatch):
    """Fake LLM agent: records calls (with leads), returns one web deal."""
    calls: list[dict] = []

    def fake_find_deals(origin, destination, dates_hint, *args, params=None, on_event=None, leads="", **kw):
        calls.append({"destination": destination, "leads": leads})
        return [FlightDeal(origin=origin, destination=destination, airline="Aegean", price=420, currency="USD",
                           depart_date="2026-11-10", return_date="2026-11-14", source="web")]

    monkeypatch.setattr(agent, "find_deals", fake_find_deals)
    monkeypatch.setattr(engine, "llm_configured", lambda: True)
    return calls


def keys(monkeypatch, serp=True, tp=True):
    if serp:
        monkeypatch.setenv("SERPAPI_API_KEY", "k")
    if tp:
        monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "t")


def providers_of(calls):
    return [p for p, _ in calls]


# --- date planning -----------------------------------------------------------------

def test_plan_dates_exact_and_flexible():
    rt = SearchParams()
    assert engine.plan_dates("2026-11-05 to 2026-11-09", rt, TODAY).pairs == [("2026-11-05", "2026-11-09")]
    assert engine.plan_dates("2026-11-05", SearchParams(trip_type="one_way"), TODAY).pairs == [("2026-11-05", "")]
    nights = SearchParams(depart_from=date(2026, 11, 5), depart_to=date(2026, 11, 5), nights_min=4, nights_max=4)
    assert engine.plan_dates("whatever", nights, TODAY).pairs == [("2026-11-05", "2026-11-09")]
    november = engine.plan_dates("November", rt, TODAY)
    assert november.pairs == [] and (november.start, november.end) == (date(2026, 11, 1), date(2026, 11, 30))
    assert november.months == ["2026-11"]
    assert engine.plan_dates("next month", rt, TODAY).months == ["2026-11"]
    anytime = engine.plan_dates("anytime in the next 3 months", rt, TODAY)
    assert anytime.months == ["2026-10", "2026-11", "2026-12"]
    assert engine.plan_dates("2026-11-05", rt, TODAY).pairs == []  # round trip without a return day
    assert engine.plan_dates("2026-09-05", SearchParams(trip_type="one_way"), TODAY).pairs == []  # past


def test_resolve_end():
    assert engine.resolve_end("TLV").serp == "TLV"
    london = engine.resolve_end("London")
    assert london.tp == "LON" and "LTN" in london.serp.split(",")
    for flexible in ("anywhere", "Greece", "Athens, Rome", "Atlantis"):
        assert engine.resolve_end(flexible) is None


# --- routing -----------------------------------------------------------------------

def test_no_keys_is_the_unchanged_llm_path(http, web):
    out = engine.search("TLV", "Prague", "2026-11-05 to 2026-11-09", TWO, today=TODAY)
    assert http == [] and len(web) == 1
    assert out.meta.sources == ["web"] and out.deals[0].airline == "Aegean"


def test_exact_dates_go_live_with_insights(http, web, monkeypatch):
    keys(monkeypatch)
    events = []
    out = engine.search("TLV", "Prague", "2026-11-05 to 2026-11-09", TWO, events.append, today=TODAY)
    assert providers_of(http) == ["serpapi"] and web == []
    assert http[0][1]["outbound_date"] == "2026-11-05" and http[0][1]["return_date"] == "2026-11-09"
    assert [d.price for d in out.deals] == [486, 612, 702]
    assert all(d.source == "google_flights" and d.price_confidence == "verified" for d in out.deals)
    assert out.deals[0].destination == "Prague" and out.deals[0].quality_flags == []  # went through normalize
    assert out.meta.sources == ["google_flights"] and out.meta.live_searches == 1
    assert (out.meta.price_insights.typical_low, out.meta.price_insights.price_level) == (540, "low")
    texts = [e["text"] for e in events]
    assert "Checking Google Flights…" in texts and "Google Flights: TLV → PRG 2026-11-05 – 2026-11-09" in texts

    again = engine.search("TLV", "Prague", "2026-11-05 to 2026-11-09", TWO, today=TODAY)
    assert len(http) == 1 and again.meta.live_searches == 0  # served from the 1h cache
    assert storage.provider_calls("serpapi") == 1


def test_budget_exhausted_falls_back_to_cache(http, web, monkeypatch):
    keys(monkeypatch)
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "0")
    out = engine.search("TLV", "Prague", "2026-11-05 to 2026-11-09", TWO, today=TODAY)
    assert providers_of(http) == ["travelpayouts"] and web == []
    assert http[0][1]["departure_at"] == "2026-11-05" and http[0][1]["return_at"] == "2026-11-09"
    assert {d.source for d in out.deals} == {"aviasales"}
    assert any("budget (0 searches) is used up" in m for m in out.meta.messages)


def test_flexible_dates_calendar_then_confirm_cheapest_live(http, web, monkeypatch):
    keys(monkeypatch)
    out = engine.search("TLV", "Prague", "November", TWO, today=TODAY)
    assert providers_of(http) == ["travelpayouts", "serpapi", "serpapi"] and web == []
    assert http[0][1]["departure_at"] == "2026-11"
    confirmed = [(p["outbound_date"], p["return_date"]) for _, p in http[1:]]
    assert confirmed == [("2026-11-26", "2026-11-29"), ("2026-11-05", "2026-11-09")]  # cheapest cached first
    cached_dates = {(d.depart_date, d.return_date) for d in out.deals if d.source == "aviasales"}
    assert cached_dates == {("2026-11-12", "2026-11-16"), ("2026-11-19", "2026-11-23")}  # confirmed ones replaced
    assert out.meta.sources == ["google_flights", "aviasales"] and out.meta.live_searches == 2


def test_flexible_dates_without_calendar_use_the_agent(http, web, monkeypatch):
    keys(monkeypatch, tp=False)
    out = engine.search("TLV", "Prague", "November", TWO, today=TODAY)
    assert http == [] and len(web) == 1 and out.meta.sources == ["web"]


def test_region_goes_to_agent_seeded_with_explore(http, web, monkeypatch):
    keys(monkeypatch)
    out = engine.search("TLV", "Greece", "November", TWO, today=TODAY)
    assert providers_of(http) == ["travelpayouts"]  # one explore call for the November window
    assert all("destination" not in p for _, p in http)
    [call] = web
    assert "ATH" in call["leads"] and "SKG" in call["leads"] and "BUD" not in call["leads"]
    dests = {d.destination_airport for d in out.deals if d.source == "aviasales"}
    assert dests == {"ATH", "SKG"}
    assert out.meta.sources == ["aviasales", "web"]


def test_unknown_region_explore_is_leads_only(http, web, monkeypatch):
    keys(monkeypatch)
    out = engine.search("TLV", "Balkans", "November", TWO, today=TODAY)
    assert "BUD" in web[0]["leads"]
    assert {d.source for d in out.deals} == {"web"}


def test_region_without_llm_shows_cached_matches(http, monkeypatch):
    keys(monkeypatch)
    monkeypatch.setattr(engine, "llm_configured", lambda: False)
    out = engine.search("TLV", "Greece", "November", TWO, today=TODAY)
    assert {d.destination_airport for d in out.deals} == {"ATH", "SKG"}


def test_provider_failure_falls_back_to_agent(http, web, monkeypatch):
    keys(monkeypatch, tp=False)

    def broken(*a, **kw):
        raise base.ProviderError("serpapi: HTTP 503")

    monkeypatch.setattr(base, "http_get_json", broken)
    out = engine.search("TLV", "Prague", "2026-11-05 to 2026-11-09", TWO, today=TODAY)
    assert len(web) == 1 and out.meta.sources == ["web"]
    assert any("unavailable" in m for m in out.meta.messages)


def test_cancel_propagates_from_provider_progress(http, web, monkeypatch):
    keys(monkeypatch)

    def on_event(event):
        if event["kind"] == "search":
            raise SearchCancelled()

    with pytest.raises(SearchCancelled):
        engine.search("TLV", "Prague", "November", TWO, on_event, today=TODAY)
    assert http == []


def test_usage_endpoint(http, web, monkeypatch):
    keys(monkeypatch)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    engine.search("TLV", "Prague", "November", TWO, today=TODAY)
    body = TestClient(main.app).get("/api/usage").json()
    serp, tp = body["providers"]
    assert (serp["provider"], serp["configured"], serp["used"], serp["budget"], serp["left"]) == \
        ("serpapi", True, 2, 230, 228)
    assert (tp["provider"], tp["used"], tp["budget"]) == ("travelpayouts", 1, None)


def test_usage_counter_is_per_month(temp_db):
    storage.record_provider_call("serpapi", datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert storage.record_provider_call("serpapi", NOW) == 1
    assert storage.record_provider_call("serpapi", NOW) == 2
    assert storage.provider_calls("serpapi", NOW) == 2
    assert storage.provider_calls("serpapi", datetime(2026, 9, 1, tzinfo=timezone.utc)) == 1


def test_cache_expires(temp_db):
    storage.cache_put("k", "serpapi", {"a": 1}, 3600, now=NOW)
    assert storage.cache_get("k", now=NOW + timedelta(minutes=59)) == {"a": 1}
    assert storage.cache_get("k", now=NOW + timedelta(minutes=61)) is None


def test_search_job_carries_meta(http, web, monkeypatch):
    import time

    keys(monkeypatch)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    client = TestClient(main.app)
    job = client.post("/api/search", json={"origin": "TLV", "destination": "Prague",
                                           "dates_hint": "2026-11-05 to 2026-11-09",
                                           "passengers": {"adults": 2}}).json()
    for _ in range(200):
        job = client.get(f"/api/jobs/{job['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "done", job
    assert job["meta"]["price_insights"]["typical_high"] == 760
    assert job["deals"][0]["source"] == "google_flights" and job["deals"][0]["seen_at"]
    [stored, *_] = client.get("/api/deals").json()
    assert stored["source"] in {"google_flights"}


# --- watch pre-screen --------------------------------------------------------------

def make_watch(**kw):
    base_kw = dict(origin="TLV", destination="Prague", dates_hint="November", max_price=300, currency="USD")
    base_kw.update(kw)
    return storage.add_watch(WatchCreate(**base_kw))


def rows(*prices, days_ago=(3, 2, 1)):
    return [{"found_at": (NOW - timedelta(days=d)).isoformat(), "price": p, "currency": "USD",
             "search_params": SearchParams().model_dump_json()} for p, d in zip(prices, days_ago)]


def test_prescreen_decisions(temp_db):
    w = make_watch()
    decide = engine.prescreen_decision
    assert decide(w, 500, [], None, None, NOW) == (False, "live search not configured")
    assert decide(w, 500, [], None, 0, NOW) == (False, "no live searches left this month")
    assert decide(w, None, [], None, 10, NOW)[0] is True
    live, why = decide(w, 340, [], NOW, 10, NOW)
    assert live and "within 15%" in why
    live, why = decide(w, 400, rows(500, 500, 500), NOW, 10, NOW)  # 340 would be 32% under typical
    assert live and "under_typical" in why
    assert decide(w, 400, [], None, 10, NOW) == (True, "first live check for this watch")
    live, why = decide(w, 400, [], NOW - timedelta(days=8), 10, NOW)
    assert live and why.startswith("weekly live check")
    live, why = decide(w, 400, [], NOW - timedelta(days=2), 10, NOW)
    assert not live and "next live check in 5 days" in why
    no_threshold = make_watch(alerts=AlertSettings(threshold=False, new_low=False, under_typical=False,
                                                   big_drop=False))
    assert decide(no_threshold, 310, [], NOW, 10, NOW)[0] is False


def test_watch_skips_live_when_cached_is_far_above(http, web, monkeypatch):
    keys(monkeypatch)
    w = make_watch(max_price=100)
    storage.log_prescreen(w.id, True, "earlier live check", 500, "USD", NOW - timedelta(days=1))
    out = engine.find_for_watch(w, watcher.watch_params(w), [], now=NOW)
    assert providers_of(http) == ["travelpayouts"] and web == []
    assert {d.source for d in out.deals} == {"aviasales"}
    assert out.meta.decision.startswith("cached: ")
    [latest, _] = storage.list_prescreens(w.id)
    assert latest["live"] is False and latest["cached_best"] == 176


def test_watch_goes_live_near_threshold(http, web, monkeypatch):
    keys(monkeypatch)
    w = make_watch(max_price=190)
    out = engine.find_for_watch(w, watcher.watch_params(w), [], now=NOW)
    assert providers_of(http) == ["travelpayouts", "serpapi"]  # calendar re-read from cache, one live check
    assert out.meta.decision.startswith("live: cached best $176")
    assert storage.last_live_check(w.id) is not None
    assert "google_flights" in out.meta.sources


def test_watch_without_keys_runs_the_agent_and_reports_prescreen(temp_db, web, monkeypatch):
    monkeypatch.setattr(watcher, "send_notification", lambda *a, **kw: [])
    w = make_watch()
    summary = watcher.run_watches(only_id=w.id, notify=False)
    [row] = summary["results"]
    assert len(web) == 1 and row["sources"] == ["web"] and row["prescreen"].startswith("no pre-screen")
