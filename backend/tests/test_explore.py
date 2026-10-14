"""Flexible dates via SerpApi Google Travel Explore (engine=google_travel_explore):
the response mapping, window/nights fitting, credit counting, fallbacks, and the
watch path ending in verified Google Flights deals + stored price insights.

Fixtures follow the documented Explore response (https://serpapi.com/google-travel-explore-api):
with an arrival_id it answers one date pair (start_date/end_date) and a few
`flights`; without one, a `destinations` list. Provider HTTP is faked."""

from __future__ import annotations

import functools
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent, alerts, engine, main, storage, watcher
from app.models import FlightDeal, Passengers, SearchParams, WatchCreate
from app.providers import base, serpapi

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
# The production watch: TLV -> London, direct, 1 adult (its 20kg checked bag left
# out here so prices stay plain fares; bag pricing is tested in test_bags).
LONDON = dict(origin="TLV", destination="London", dates_hint="October - November after October 11th",
              max_stops=0, currency="USD")
LONDON_PARAMS = SearchParams(max_stops=0)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


EXPLORE_BY_MONTH = {"10": "serpapi_explore_october.json", "11": "serpapi_explore_november.json",
                    "0": "serpapi_explore_november.json"}


@pytest.fixture
def http(temp_db, monkeypatch):
    """Fixture-backed SerpApi: Explore by `month`, Google Flights for London.
    Records (engine, params) per call; `http.explore` swaps Explore answers."""
    calls = Calls()
    calls.explore = explore = dict(EXPLORE_BY_MONTH)

    def fake(url, params, headers=None, *, provider):
        assert provider == "serpapi", "no Travelpayouts calls without its token"
        calls.append((params["engine"], dict(params)))
        if params["engine"] == "google_travel_explore":
            name = explore.get(str(params["month"]))
            return load(name) if name else {"search_metadata": {"status": "Success"}, "flights": []}
        return load("serpapi_round_trip_london.json")

    monkeypatch.setattr(base, "http_get_json", fake)
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    return calls


class Calls(list):
    explore: dict

    def engines(self):
        return [e for e, _ in self]


@pytest.fixture
def web(monkeypatch):
    calls: list[str] = []

    def fake_find_deals(origin, destination, dates_hint, *args, params=None, on_event=None, leads="", **kw):
        calls.append(destination)
        return [FlightDeal(origin=origin, destination=destination, airline="Web Air", price=700, currency="USD",
                           depart_date="2026-11-10", return_date="2026-11-17", source="web")]

    monkeypatch.setattr(agent, "find_deals", fake_find_deals)
    monkeypatch.setattr(engine, "llm_configured", lambda: True)
    return calls


# --- provider: request + mapping ----------------------------------------------------

def test_explore_request_params():
    p = serpapi.explore_request_params("TLV", serpapi.explore_place("LON", "LHR,LGW"), LONDON_PARAMS, 11)
    assert p == {"engine": "google_travel_explore", "departure_id": "TLV", "arrival_id": "/m/04jpl", "type": "1",
                 "month": 11, "travel_duration": 2, "travel_class": "1", "adults": 1, "currency": "USD",
                 "hl": "en", "stops": "1"}  # stops 1 = nonstop, as on google_flights
    party = SearchParams(trip_type="one_way", passengers=Passengers(adults=2, children=1, infants=1),
                         cabin_class="business", cabin_bag=True)
    q = serpapi.explore_request_params("TLV", "PRG", party, 0)
    assert "travel_duration" not in q and q["type"] == "2" and q["travel_class"] == "3"
    assert (q["adults"], q["children"], q["infants_on_lap"], q["bags"], q["stops"]) == (2, 1, 1, 3, "0")
    assert serpapi.explore_place("BRU/CRL", "BRU,CRL") == "BRU,CRL"  # no kgmid: the airport list


def test_map_explore_flights_mode_filters_to_the_city():
    two = SearchParams(passengers=Passengers(adults=2), checked_bag=True)
    deals = serpapi.map_explore(load("serpapi_explore_november.json"), "TLV", two,
                                ("LHR", "LGW", "STN", "LTN", "LCY", "SEN"))
    assert [(d.destination_airport, d.price) for d in deals] == [("LTN", 389), ("LGW", 455), ("LHR", 612)]
    w6 = deals[0]
    assert (w6.depart_date, w6.return_date, w6.stops, w6.airline_code, w6.duration) == \
        ("2026-11-17", "2026-11-24", 0, "W6", "5h 30m")
    assert w6.source == "google_explore" and w6.price_confidence == "estimated"
    assert w6.price_per_person == 194.5
    assert w6.booking_url.endswith("tfs=explore-11")  # the google_flights_link for that pair
    assert "not a confirmed itinerary" in w6.notes and "Checked 20kg bag NOT included" in w6.notes
    assert not w6.booking_token and not w6.departure_token  # nothing to book from Explore


def test_map_explore_destinations_mode():
    data = load("serpapi_explore_destinations.json")
    [london] = serpapi.map_explore(data, "TLV", SearchParams(), ("LHR", "LTN"))
    assert (london.destination_airport, london.price, london.depart_date) == ("LTN", 401, "2026-11-17")
    everything = serpapi.map_explore(data, "TLV", SearchParams())
    assert [d.airline_code for d in everything] == ["", "W6"]  # "multi" is no IATA code


def test_one_way_explore_ignores_the_return():
    deals = serpapi.map_explore(load("serpapi_explore_november.json"), "TLV",
                                SearchParams(trip_type="one_way"), ("LTN",))
    assert deals[0].return_date == ""


def test_explore_trim_keeps_what_mapping_needs(http):
    serpapi.explore("TLV", "/m/04jpl", SearchParams(), 11, members=("LTN",))
    again = serpapi.explore("TLV", "/m/04jpl", SearchParams(), 11, members=("LTN",))
    assert again.cached and again.paid_calls == 0 and again.deals[0].price == 389
    assert len(http) == 1 and storage.provider_calls("serpapi") == 1


# --- window, nights, months ---------------------------------------------------------------

def test_watch_window_text_is_a_window_not_a_date():
    plan = engine.plan_dates(LONDON["dates_hint"], LONDON_PARAMS, TODAY)
    assert plan.pairs == [] and (plan.start, plan.end) == (date(2026, 10, 12), date(2026, 11, 30))
    assert engine.explore_months(plan, TODAY) == [10, 11]
    later = engine.plan_dates(LONDON["dates_hint"], LONDON_PARAMS, date(2026, 11, 2))
    assert (later.start, later.end) == (date(2026, 11, 2), date(2026, 11, 30))  # not October 2027
    until = engine.plan_dates("until Nov 20", SearchParams(), TODAY)
    assert (until.start, until.end) == (date(2026, 10, 2), date(2026, 11, 20))
    between = engine.plan_dates("between 3 Nov and 20 Nov", SearchParams(), TODAY)
    assert (between.start, between.end) == (date(2026, 11, 3), date(2026, 11, 20))
    # unchanged: an ISO pair is exact, a single day is that day
    assert engine.plan_dates("2026-11-05 to 2026-11-09", SearchParams(), TODAY).pairs == [("2026-11-05", "2026-11-09")]
    assert engine.plan_dates("October 11 week", SearchParams(), TODAY).start == date(2026, 10, 11)


def test_explore_months_stay_within_budget_and_horizon():
    def months(hint):
        return engine.explore_months(engine.plan_dates(hint, SearchParams(), TODAY), TODAY)

    assert months("November") == [11]
    assert months("anytime in the next 3 months") == [0]  # 3 months: one "next six months" call
    assert months("June 2027") == []                       # beyond Explore's six months


def test_explore_duration_from_trip_length():
    d = engine.explore_duration
    assert d("a weekend in November", SearchParams()) == serpapi.WEEKEND
    assert d("November", SearchParams(nights_min=2, nights_max=3)) == serpapi.WEEKEND
    assert d("November", SearchParams(nights_min=5, nights_max=8)) == serpapi.ONE_WEEK
    assert d("November", SearchParams(nights_min=12)) == serpapi.TWO_WEEKS
    assert d("November", SearchParams()) == serpapi.ONE_WEEK


def test_fit_pair_window_and_nights():
    plan = engine.plan_dates(LONDON["dates_hint"], LONDON_PARAMS, TODAY)
    deal = FlightDeal(origin="TLV", destination="LTN", airline="W", price=1, currency="USD",
                      depart_date="2026-11-17", return_date="2026-11-24")
    assert engine._fit_pair(deal, plan, SearchParams()) == (("2026-11-17", "2026-11-24"), True)
    assert engine._fit_pair(deal, plan, SearchParams(nights_min=3, nights_max=4)) == (("2026-11-17", "2026-11-21"), False)
    early = deal.model_copy(update={"depart_date": "2026-10-06", "return_date": "2026-10-13"})
    assert engine._fit_pair(early, plan, SearchParams()) == (None, False)


# --- search: Explore -> confirm ---------------------------------------------------------

def test_london_window_explores_two_months_then_confirms_one(http, web):
    events = []
    out = engine.search("TLV", "London", LONDON["dates_hint"], LONDON_PARAMS, events.append, today=TODAY)
    assert http.engines() == ["google_travel_explore", "google_travel_explore", "google_flights"]
    assert [p["month"] for _, p in http[:2]] == [10, 11]
    flights = http[2][1]
    # October's Explore pair (Oct 6) is before the window; November's is confirmed
    assert (flights["outbound_date"], flights["return_date"], flights["stops"]) == ("2026-11-17", "2026-11-24", "1")
    assert flights["arrival_id"] == "LHR,LGW,STN,LTN,LCY,SEN"
    assert out.meta.live_searches == 3 <= engine.MAX_FLEX_CREDITS
    assert web == [] and out.meta.sources == ["google_flights"]
    assert all(d.source == "google_flights" and d.depart_date == "2026-11-17" for d in out.deals)
    assert out.meta.price_insights is not None
    texts = [e["text"] for e in events]
    assert "Finding the cheapest dates on Google…" in texts
    assert "Google Explore: TLV → LON, Nov 2026" in texts
    # fare-API calls are tagged, so the console counts "3 Google searches", not web searches
    assert [e.get("source") for e in events if e["kind"] == "search"] == ["google", "google", "google"]
    assert all("source" not in e for e in events if e["kind"] == "phase")


def test_one_month_window_spends_two(http, web):
    out = engine.search("TLV", "London", "November", SearchParams(), today=TODAY)
    assert http.engines() == ["google_travel_explore", "google_flights"]
    assert out.meta.live_searches == 2


def test_nights_outside_explores_pair_move_the_return(http, web):
    out = engine.search("TLV", "London", "November", SearchParams(nights_min=4, nights_max=4), today=TODAY)
    flights = http[-1][1]
    assert (flights["outbound_date"], flights["return_date"]) == ("2026-11-17", "2026-11-21")
    assert {d.source for d in out.deals} == {"google_flights"}  # Explore's 7-night price isn't shown


def test_unconfirmed_explore_prices_show_as_estimates(http, web, monkeypatch):
    real = base.http_get_json

    def flights_down(url, params, headers=None, *, provider):
        if params["engine"] == "google_flights":
            raise base.ProviderError("serpapi: HTTP 503")
        return real(url, params, headers, provider=provider)

    monkeypatch.setattr(base, "http_get_json", flights_down)
    out = engine.search("TLV", "London", "November", SearchParams(), today=TODAY)
    assert web == [] and {d.source for d in out.deals} == {"google_explore"}
    assert all(d.price_confidence == "estimated" for d in out.deals)
    assert out.meta.sources == ["google_explore"]


def test_explore_finds_nothing_falls_back_to_web(http, web):
    http.explore.clear()
    out = engine.search("TLV", "London", "November", SearchParams(), today=TODAY)
    assert http.engines() == ["google_travel_explore"] and web == ["London"]
    assert out.meta.sources == ["web"]


def test_budget_exhausted_falls_back_to_web(http, web, monkeypatch):
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "0")
    out = engine.search("TLV", "London", "November", SearchParams(), today=TODAY)
    assert http == [] and web == ["London"]
    assert any("budget (0 searches) is used up" in m for m in out.meta.messages)


def test_calendar_keeps_priority_when_configured(temp_db, web, monkeypatch):
    """Travelpayouts (free) still picks the dates when configured; no Explore."""
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "t")
    calls = []

    def fake(url, params, headers=None, *, provider):
        calls.append(params.get("engine", provider))
        if provider == "serpapi":
            return load("serpapi_round_trip.json")
        return load("travelpayouts_calendar.json")

    monkeypatch.setattr(base, "http_get_json", fake)
    engine.search("TLV", "Prague", "November", SearchParams(), today=TODAY)
    assert "google_travel_explore" not in calls and calls[0] == "travelpayouts"


# --- watches ----------------------------------------------------------------------

def london_watch(**kw):
    return storage.add_watch(WatchCreate(**{**LONDON, "max_price": 450, **kw}))


def test_watch_run_ends_verified_with_price_insights(http, web, monkeypatch):
    monkeypatch.setattr(engine, "find_for_watch", functools.partial(engine.find_for_watch, now=NOW))
    monkeypatch.setattr(watcher, "send_notification", lambda *a, **kw: [])
    w = london_watch()
    summary = watcher.run_watches(only_id=w.id, notify=False)
    [row] = summary["results"]
    assert row["sources"] == ["google_flights"] and row["prescreen"].startswith("live: ")
    assert http.engines() == ["google_travel_explore", "google_travel_explore", "google_flights"]
    assert web == []
    [saved] = storage.watch_price_insights()[w.id]
    assert saved["kind"] == alerts.insight_kind(watcher.watch_params(w))
    stored = storage.watch_run_deals(w.id)
    assert stored and all(d.source == "google_flights" for d in stored)
    assert any(d.departure_token or d.booking_token for d in stored)  # details / book direct work

    stats = alerts.watch_stats(storage.get_watch(w.id), storage.watch_sightings()[w.id], NOW,
                               storage.watch_price_insights()[w.id])
    assert stats.basis == "google" and stats.history_points > 0 and stats.basis_runs == 1


def test_watch_explore_prescreen_skips_the_confirmation(http, web, monkeypatch):
    w = london_watch(max_price=100)
    # ~35 days out: the credit plan checks every 3 days, so today is due.
    storage.log_prescreen(w.id, True, "earlier live check", 400, "USD", NOW - timedelta(days=3))
    out = engine.find_for_watch(w, watcher.watch_params(w), [], now=NOW)
    assert http.engines() == ["google_travel_explore", "google_travel_explore"]  # 2 credits, no confirm
    assert out.meta.decision.startswith("explore: Google Explore best $389 is over 15% above")
    assert {d.source for d in out.deals} == {"google_explore"} and out.meta.live_searches == 2
    [latest, _] = storage.list_prescreens(w.id)
    assert latest["live"] is False and latest["cached_best"] == 389


def test_watch_skipped_when_its_plan_says_not_today(http, web):
    w = london_watch(max_price=100)
    storage.log_prescreen(w.id, True, "earlier live check", 400, "USD", NOW - timedelta(days=1))
    out = engine.find_for_watch(w, watcher.watch_params(w), [], now=NOW)
    assert http.engines() == [] and web == [] and out.deals == []  # no paid pre-screen either
    assert out.meta.skipped and out.meta.decision.startswith("skipped: plan: in 2 days")
    [latest, _] = storage.list_prescreens(w.id)
    assert latest["live"] is False and latest["reason"].startswith("plan: in 2 days")
    # "Run now" ignores the plan.
    forced = engine.find_for_watch(w, watcher.watch_params(w), [], now=NOW, force=True)
    assert not forced.meta.skipped and http.engines()[:2] == ["google_travel_explore", "google_travel_explore"]


def test_watch_explore_prescreen_goes_live_near_threshold(http, web):
    w = london_watch(max_price=400)
    out = engine.find_for_watch(w, watcher.watch_params(w), [], now=NOW)
    assert out.meta.decision.startswith("live: Google Explore best $389 is within 15%")
    assert http.engines()[-1] == "google_flights" and out.meta.live_searches == 3
    assert "google_flights" in out.meta.sources


def test_explore_rows_are_their_own_tier():
    rows = [{"found_at": NOW - timedelta(days=i), "price": p, "currency": "USD", "search_params": None,
             "source": src} for i, (p, src) in enumerate([(300, "google_explore"), (320, "google_explore"),
                                                           (500, "aviasales")], start=1)]
    runs = alerts.run_bests(rows, "USD", alerts.fare_kind(None))
    assert {r.tier for r in runs} == {"explore", "cached"}
    assert alerts.select_baseline(runs).basis == "explore"
    verified = runs + [alerts.RunBest(NOW, 350, alerts.VERIFIED)]
    assert alerts.select_baseline(verified).basis == "verified"  # Explore never counts as verified


# --- credits & usage protection ---------------------------------------------------------

def test_watch_credit_estimates(temp_db, monkeypatch):
    w = london_watch()
    assert engine.watch_credits(w, TODAY).per_check == 0  # no SerpApi key
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    c = engine.watch_credits(w, TODAY)
    assert (c.per_check, c.per_month, c.text) == (3, 90, "Up to 3 live searches per check (~90/month)")
    assert engine.watch_credits(w, date(2026, 11, 2)).per_check == 2  # only November left
    exact = london_watch(dates_hint="2026-11-05 to 2026-11-09")
    assert engine.watch_credits(exact, TODAY).per_check == 1
    region = london_watch(destination="Greece")
    assert engine.watch_credits(region, TODAY).per_check == 0
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "t")
    assert engine.watch_credits(w, TODAY).per_check == 1  # the free calendar picks the dates


def test_usage_warns_when_watches_exceed_budget(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    london_watch()
    client = TestClient(main.app)
    body = client.get("/api/usage").json()
    assert body["watch_projection"] > 0 and body["warning"] == ""
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "40")
    body = client.get("/api/usage").json()
    assert body["warning"] == "" and body["watch_projection"] <= 28  # the fair share caps it (70% of 40)
    client.patch(f"/api/watches/{storage.list_watches()[0].id}", json={"credit_budget": 60})
    body = client.get("/api/usage").json()
    assert "over the 40-search budget" in body["warning"]
    [watch] = client.get("/api/watches").json()
    assert watch["credits"]["per_check"] >= 2 and "live searches per check" in watch["credits"]["text"]


def test_digest_carries_the_budget_warning(temp_db):
    w = london_watch()
    _, plain, rich = alerts.format_digest([alerts.WatchResult(watch=w)], NOW, "Over budget <soon>")
    assert plain.endswith("⚠️ Over budget <soon>") and "Over budget &lt;soon&gt;" in rich
    _, plain, _ = alerts.format_digest([alerts.WatchResult(watch=w)], NOW)
    assert "⚠️" not in plain
