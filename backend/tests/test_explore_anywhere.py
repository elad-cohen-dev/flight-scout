"""Explore mode ("anywhere from TLV", app/explore.py): Google Travel Explore
without an arrival_id -> a destinations list for the map.

The fixture follows the documented response (https://serpapi.com/google-travel-explore-api):
`destinations[]` with destination_id, name, country, gps_coordinates, thumbnail,
destination_airport.code, start_date (+ end_date on round trips — sometimes
missing), flight_price, flight_duration, number_of_stops, airline, airline_code.
Provider HTTP is faked; no key is real."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import explore, main, storage
from app.models import Passengers
from app.providers import base, serpapi

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 1)
SEEN = "2026-10-01T05:00:00+00:00"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def req(**kw) -> explore.ExploreRequest:
    return explore.ExploreRequest(**kw)


@pytest.fixture
def http(temp_db, monkeypatch):
    calls: list[dict] = []

    def fake(url, params, headers=None, *, provider):
        calls.append(dict(params))
        return load("serpapi_explore_anywhere.json")

    monkeypatch.setattr(base, "http_get_json", fake)
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return calls


# --- request -------------------------------------------------------------------

def test_request_params_open_destination():
    p = explore.request_params(req(origin="TLV", month=11, travel_duration="weekend", max_stops=0,
                                   passengers=Passengers(adults=2, children=1, infants=1), cabin_bag=True,
                                   currency="EUR", max_price=300, region="europe"), TODAY)
    assert p["engine"] == "google_travel_explore"
    assert "arrival_id" not in p  # no destination = the destinations list
    assert p["departure_id"] == "TLV"
    assert (p["month"], p["travel_duration"], p["type"]) == (11, 1, "1")
    assert p["stops"] == "1"  # nonstop
    assert (p["adults"], p["children"], p["infants_on_lap"], p["bags"]) == (2, 1, 1, 3)
    assert p["currency"] == "EUR"
    # Applied after the fetch, so changing them never spends a search:
    assert not {"max_price", "region", "country"} & set(p)


def test_request_params_one_way_metro_origin_and_durations():
    p = explore.request_params(req(origin="London [LON]", trip_type="one_way"), TODAY)
    assert p["departure_id"] == "/m/04jpl"  # the city kgmid: every London airport
    assert p["type"] == "2" and "travel_duration" not in p
    assert explore.request_params(req(travel_duration="two_weeks"), TODAY)["travel_duration"] == 3
    assert explore.request_params(req(), TODAY)["month"] == 0


def test_month_outside_explores_horizon_is_refused():
    assert explore.months_ahead(TODAY) == [10, 11, 12, 1, 2, 3]
    with pytest.raises(explore.ExploreError) as err:
        explore.request_params(req(month=6), TODAY)
    assert err.value.status == 422
    explore.request_params(req(month=3), TODAY)  # March 2027 is within six months


def test_unknown_origin_is_refused():
    with pytest.raises(explore.ExploreError) as err:
        explore.request_params(req(origin="Atlantis"), TODAY)
    assert err.value.status == 422


# --- mapping -------------------------------------------------------------------

def mapped(**kw):
    return explore.map_destinations(load("serpapi_explore_anywhere.json"), req(**kw), "TLV", SEEN)


def test_destinations_are_normalized_cheapest_first_one_per_city():
    dests = mapped()
    assert [d.city for d in dests] == ["Paphos", "Athens", "Budapest", "Dubai", "London", "Bangkok", "New York"]
    london = next(d for d in dests if d.city == "London")
    assert london.airport == "LTN" and london.price == 401  # the cheaper London airport wins
    assert london.search_destination == "London [LON]"  # live check covers all London airports
    assert (london.country, london.country_code, london.region) == ("United Kingdom", "GB", "europe")
    assert (london.lat, london.lon) == (51.5072178, -0.1275862)
    assert london.thumbnail.startswith("https://")
    assert (london.depart_date, london.return_date, london.nights, london.return_estimated) == \
        ("2026-11-17", "2026-11-24", 7, False)
    assert (london.airline, london.airline_code, london.stops, london.duration, london.duration_minutes) == \
        ("Wizz Air", "W6", 0, "5h 30m", 330)
    assert london.source == "google_explore" and london.price_confidence == "estimated"
    kinds = {link.kind for link in london.booking_links}
    assert "google_flights" in kinds


def test_missing_return_is_estimated_from_trip_length_and_missing_coords_kept():
    dests = {d.city: d for d in mapped(travel_duration="weekend")}
    paphos = dests["Paphos"]
    assert (paphos.return_date, paphos.return_estimated, paphos.nights) == ("2026-10-25", True, 2)
    dubai = dests["Dubai"]
    assert dubai.lat is None and dubai.lon is None  # listed, not on the map
    assert (dubai.region, dubai.search_destination) == ("middle_east", "Dubai [DXB]")
    assert dests["New York"].search_destination == "New York [NYC]"
    assert dests["Budapest"].airline_code == ""  # "multi" is not an airline
    assert "Nowhere" not in dests and "Tbilisi" not in dests  # unpriced / undated


def test_party_price_and_one_way():
    dests = {d.city: d for d in mapped(passengers=Passengers(adults=2, infants=1), trip_type="one_way")}
    athens = dests["Athens"]
    assert athens.price == 158 and athens.price_per_person == 79  # lap infants don't pay a seat
    assert athens.return_date == "" and athens.nights is None


def test_filters_after_the_fetch():
    dests = mapped()
    assert [d.city for d in explore.apply_filters(dests, req(max_price=200))] == ["Paphos", "Athens", "Budapest"]
    assert [d.city for d in explore.apply_filters(dests, req(region="asia"))] == ["Bangkok"]
    assert [d.city for d in explore.apply_filters(dests, req(region="americas"))] == ["New York"]
    assert [d.city for d in explore.apply_filters(dests, req(country="GR"))] == ["Athens"]
    assert [d.city for d in explore.apply_filters(dests, req(country="cyprus"))] == ["Paphos"]
    assert [d.city for d in explore.apply_filters(dests, req(country="יוון"))] == ["Athens"]


# --- credits & caching -----------------------------------------------------------

def test_explore_spends_one_search_then_is_free_for_six_hours(http):
    client = TestClient(main.app)
    body = {"origin": "TLV", "month": 0, "travel_duration": "week"}
    # The tab's free auto-load on open never spends a search:
    assert client.post("/api/explore", json={**body, "cached_only": True}).status_code == 404
    assert http == []
    q = client.post("/api/explore/quote", json=body).json()
    assert q == {"configured": True, "cached": False, "cost": 1, "left": 230, "budget": 230}

    first = client.post("/api/explore", json=body).json()
    assert first["live_searches"] == 1 and first["cached"] is False
    assert first["total"] == 7 and len(first["destinations"]) == 7
    assert first["left"] == 229
    assert len(http) == 1 and http[0]["api_key"] == "k"
    assert storage.provider_calls(serpapi.PROVIDER) == 1

    # Price cap / region are free: same cached response.
    again = client.post("/api/explore", json={**body, "max_price": 300, "region": "europe"}).json()
    assert again["cached"] is True and again["live_searches"] == 0
    assert [d["city"] for d in again["destinations"]] == ["Paphos", "Athens", "Budapest"]
    assert again["total"] == 7
    assert again["fetched_at"] == "2026-10-01T05:00:00+00:00"  # Google's answer time
    assert len(http) == 1
    assert client.post("/api/explore/quote", json={**body, "region": "asia"}).json()["cost"] == 0
    assert client.post("/api/explore", json={**body, "cached_only": True}).json()["cached"] is True

    # Anything Google prices differently is a new search.
    assert client.post("/api/explore/quote", json={**body, "max_stops": 0}).json()["cost"] == 1


def test_cache_lasts_six_hours(http):
    explore.explore(req())
    key = base.cache_key(serpapi.PROVIDER, explore.request_params(req()))
    now = datetime.now(timezone.utc)
    assert storage.cache_get(key, now + timedelta(hours=5, minutes=50)) is not None
    assert storage.cache_get(key, now + timedelta(hours=6, minutes=10)) is None


def test_budget_used_up_is_409_and_unconfigured_is_503(http, monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "0")
    res = client.post("/api/explore", json={})
    assert res.status_code == 409 and "used up" in res.json()["detail"]
    assert client.post("/api/explore/quote", json={}).json()["left"] == 0
    assert http == []
    monkeypatch.delenv("SERPAPI_API_KEY")
    assert client.post("/api/explore", json={}).status_code == 503
    assert client.post("/api/explore/quote", json={}).json() == \
        {"configured": False, "cached": False, "cost": 0, "left": 0, "budget": 0}  # budget env is still 0


def test_provider_error_is_502_without_the_key(http, monkeypatch):
    def broken(url, params, headers=None, *, provider):
        raise base.ProviderError("serpapi: HTTP 500 boom")

    monkeypatch.setattr(base, "http_get_json", broken)
    res = TestClient(main.app).post("/api/explore", json={"origin": "TLV"})
    assert res.status_code == 502 and "k" not in res.json()["detail"].split()


def test_no_destinations_message(http, monkeypatch):
    monkeypatch.setattr(base, "http_get_json",
                        lambda url, params, headers=None, *, provider: {"search_metadata": {}, "destinations": []})
    out = explore.explore(req())
    assert out.destinations == [] and out.total == 0 and out.messages
