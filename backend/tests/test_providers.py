"""Fare-provider mapping (SerpApi Google Flights, Travelpayouts/Aviasales) from
fixture responses shaped like the documented formats. No network: every
request goes through providers.base.http_get_json, which is faked."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.models import Passengers, SearchParams
from app.providers import base, serpapi, travelpayouts
from app.providers.base import FareQuery, ProviderError

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "serp-secret-key")
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "tp-secret-token")
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "12345")


@pytest.fixture
def http(monkeypatch):
    """Queue of canned responses; records every request."""
    calls: list[dict] = []
    responses: list[dict] = []

    def fake(url, params, headers=None, *, provider):
        calls.append({"url": url, "params": dict(params), "headers": dict(headers or {}), "provider": provider})
        return responses.pop(0)

    monkeypatch.setattr(base, "http_get_json", fake)
    return calls, responses


# --- SerpApi -----------------------------------------------------------------------

def test_serpapi_request_params_cover_the_criteria():
    params = SearchParams(passengers=Passengers(adults=2, children=1, infants=1), cabin_class="business",
                          max_stops=1, cabin_bag=True, currency="eur")
    out = serpapi.request_params(FareQuery("TLV", "PRG", "2026-11-05", "2026-11-09", params))
    assert out["type"] == "1" and out["return_date"] == "2026-11-09"
    assert (out["travel_class"], out["stops"], out["currency"]) == ("3", "2", "EUR")
    assert (out["adults"], out["children"], out["infants_on_lap"], out["bags"]) == (2, 1, 1, 3)
    one_way = serpapi.request_params(FareQuery("TLV", "LTN", "2026-11-12", "", SearchParams(max_stops=0)))
    assert one_way["type"] == "2" and "return_date" not in one_way and one_way["stops"] == "1"
    assert "bags" not in one_way and "children" not in one_way
    assert serpapi.request_params(FareQuery("TLV", "LTN", "2026-11-12", "", SearchParams()))["stops"] == "0"


def test_serpapi_round_trip_two_adults_maps_party_totals():
    params = SearchParams(passengers=Passengers(adults=2))
    query = FareQuery("TLV", "PRG", "2026-11-05", "2026-11-09", params)
    deals, tokens = serpapi.map_response(load("serpapi_round_trip.json"), query, "2026-10-01T08:12:40+00:00")
    assert [d.price for d in deals] == [486, 612, 702]  # unpriced/segmentless entry dropped, sorted
    direct = deals[0]
    assert (direct.airline, direct.airline_code, direct.flight_numbers) == ("Smartwings", "QS", "QS 1285")
    assert (direct.origin_airport, direct.destination_airport) == ("TLV", "PRG")
    assert (direct.depart_date, direct.depart_time, direct.arrive_time) == ("2026-11-05", "06:10", "09:25")
    assert direct.return_date == "2026-11-09" and direct.return_depart_time == ""
    assert direct.price_per_person == 243 and direct.currency == "USD"
    assert (direct.duration, direct.stops) == ("4h 15m", 0)
    assert direct.price_confidence == "verified" and direct.source == "google_flights"
    assert direct.seen_at == "2026-10-01T08:12:40+00:00"
    assert direct.booking_url.startswith("https://www.google.com/travel/flights") and "tfs=" in direct.booking_url
    assert "total for 2 travellers" in direct.notes and "Checked baggage for a fee" in direct.notes
    via = deals[1]
    assert (via.stops, via.flight_numbers, via.arrive_time, via.duration) == (1, "OS 858 / OS 703", "10:25", "6h 25m")
    assert "via VIE 1h 25m" in via.notes and "Often delayed" in via.notes
    assert set(tokens) == {0, 1, 2}


def test_serpapi_one_way_bags_and_overnight():
    params = SearchParams(trip_type="one_way", cabin_bag=True, currency="GBP")
    query = FareQuery("TLV", "LTN", "2026-11-12", "", params)
    deals, tokens = serpapi.map_response(load("serpapi_one_way.json"), query)
    wizz, connecting = deals
    assert wizz.return_date == "" and wizz.currency == "GBP" and wizz.price == 131
    assert (wizz.airline_code, wizz.arrive_time) == ("W9", "00:30")
    assert "Arrives 2026-11-13" in wizz.notes and "Carry-on bag fee included in price" in wizz.notes
    assert "Price includes carry-on bag fees" in wizz.notes and wizz.price_confidence == "verified"
    assert connecting.airline == "Turkish Airlines + Pegasus" and connecting.airline_code == ""
    assert "via IST 5h 20m (overnight)" in connecting.notes
    assert tokens == {}  # one-way results carry booking tokens, not departure tokens

    checked = SearchParams(trip_type="one_way", checked_bag=True, currency="GBP")
    [first, _] = serpapi.map_response(load("serpapi_one_way.json"), FareQuery("TLV", "LTN", "2026-11-12", "", checked))[0]
    assert first.price_confidence == "estimated" and "Checked 20kg bag NOT included" in first.notes


def test_serpapi_insights():
    query = FareQuery("TLV", "PRG", "2026-11-05", "2026-11-09", SearchParams(passengers=Passengers(adults=2)))
    insights = serpapi.map_insights(load("serpapi_round_trip.json"), query)
    assert (insights.lowest_price, insights.price_level, insights.typical_low, insights.typical_high) == \
        (486, "low", 540, 760)
    assert insights.route == "TLV>PRG" and insights.currency == "USD"
    assert serpapi.map_insights({}, query) is None


def test_serpapi_search_counts_caches_and_never_sends_key_to_cache(temp_db, keys, http):
    calls, responses = http
    responses.append(load("serpapi_round_trip.json"))
    query = FareQuery("TLV", "PRG", "2026-11-05", "2026-11-09", SearchParams(passengers=Passengers(adults=2)))
    first = serpapi.search(query)
    assert first.paid_calls == 1 and not first.cached and len(first.deals) == 3
    assert calls[0]["params"]["api_key"] == "serp-secret-key"
    again = serpapi.search(query)  # within the hour: our cache, free
    assert again.cached and again.paid_calls == 0 and len(calls) == 1
    assert [d.price for d in again.deals] == [d.price for d in first.deals]
    assert again.insights.typical_low == 540
    assert serpapi.used_this_month() == 1 and serpapi.remaining() == 229
    from app import storage
    rows = storage._with_conn(lambda c: c.execute("SELECT cache_key, payload FROM provider_cache").fetchall())
    assert rows and all("serp-secret-key" not in key + payload for key, payload in rows)


def test_serpapi_no_results_is_empty_not_an_error(temp_db, keys, http):
    _, responses = http
    responses.append(load("serpapi_no_results.json"))
    result = serpapi.search(FareQuery("TLV", "PRG", "2026-11-05", "", SearchParams(trip_type="one_way")))
    assert result.deals == [] and result.insights is None


def test_serpapi_error_raises(temp_db, keys, http):
    _, responses = http
    responses.append({"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"})
    with pytest.raises(ProviderError):
        serpapi.search(FareQuery("TLV", "PRG", "2026-11-05", "", SearchParams(trip_type="one_way")))


def test_serpapi_budget_exhausted(temp_db, keys, http, monkeypatch):
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "0")
    with pytest.raises(base.BudgetExhausted):
        serpapi.search(FareQuery("TLV", "PRG", "2026-11-05", "", SearchParams(trip_type="one_way")))
    assert http[0] == []


def test_serpapi_return_leg_fills_times(temp_db, keys, http):
    calls, responses = http
    responses += [load("serpapi_round_trip.json"), load("serpapi_return_flights.json")]
    query = FareQuery("TLV", "PRG", "2026-11-05", "2026-11-09", SearchParams(passengers=Passengers(adults=2)))
    result = serpapi.search(query)
    deal, paid = serpapi.with_return_leg(result.deals[0], query, result.departure_tokens[0])
    assert paid == 1 and calls[1]["params"]["departure_token"] == result.departure_tokens[0]
    assert deal.return_depart_time == "10:25" and deal.flight_numbers == "QS 1285 / QS 1286"
    assert deal.price == 486


def test_http_errors_never_leak_the_key(monkeypatch):
    def boom(url, params=None, **kw):
        raise httpx.ConnectError(f"cannot reach {url}?api_key={params['api_key']}")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(ProviderError) as err:
        base.http_get_json(serpapi.URL, {"api_key": "serp-secret-key"}, provider="serpapi")
    assert "serp-secret-key" not in str(err.value)

    class Bad:
        status_code = 401
        text = "Invalid API key"

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: Bad())
    with pytest.raises(ProviderError, match="HTTP 401"):
        base.http_get_json(serpapi.URL, {"api_key": "x"}, provider="serpapi")


def test_cache_key_ignores_credentials_and_order():
    a = base.cache_key("serpapi", {"api_key": "a", "departure_id": "TLV", "adults": 2})
    b = base.cache_key("serpapi", {"adults": "2", "departure_id": "TLV", "api_key": "b"})
    assert a == b and a != base.cache_key("serpapi", {"departure_id": "TLV", "adults": 3})


# --- Travelpayouts -----------------------------------------------------------------

def test_travelpayouts_request_params():
    rt = travelpayouts.request_params(FareQuery("TLV", "PRG", "2026-11", "", SearchParams(max_stops=0, currency="ILS")))
    assert rt == {"origin": "TLV", "destination": "PRG", "departure_at": "2026-11", "one_way": "false",
                  "direct": "true", "currency": "ils", "sorting": "price", "unique": "false", "limit": 100}
    explore = travelpayouts.request_params(FareQuery("TLV", "", "2026-11", "", SearchParams(trip_type="one_way")),
                                           explore=True)
    assert "destination" not in explore and explore["one_way"] == "true"


def test_travelpayouts_maps_per_adult_prices_to_party_totals(keys):
    params = SearchParams(passengers=Passengers(adults=2, children=1, infants=1))
    deals = travelpayouts.map_response(load("travelpayouts_calendar.json"), params)
    qs = deals[0]
    assert qs.price == round(198 * 3 + 198 * 0.1, 2) and qs.price_per_person == 198 and qs.currency == "USD"
    assert (qs.airline, qs.airline_code, qs.flight_numbers) == ("Smartwings", "QS", "QS 1285")
    assert (qs.depart_date, qs.depart_time, qs.return_date, qs.return_depart_time) == \
        ("2026-11-05", "06:10", "2026-11-09", "10:25")
    assert (qs.duration, qs.stops) == ("4h 15m", 0)
    assert qs.price_confidence == "estimated" and qs.source == "aviasales"
    assert qs.booking_url.startswith("https://www.aviasales.com/search/TLV0511PRG0911211?t=")  # 2 adults, 1 child, 1 infant
    assert qs.booking_url.endswith("&marker=12345") and qs.source_url == qs.booking_url
    assert "198 USD per adult x 4 travellers" in qs.notes and "lap infants" in qs.notes
    assert deals[2].flight_numbers == "LY 2521"  # integer flight_number
    assert deals[3].stops == 1 and "return 1 stop" in deals[3].notes


def test_travelpayouts_one_way_drops_return_and_flags_bags():
    params = SearchParams(trip_type="one_way", cabin_bag=True)
    [first, *_] = travelpayouts.map_response(load("travelpayouts_calendar.json"), params)
    assert first.return_date == "" and first.price == 198
    assert "bags NOT included" in first.notes


def test_travelpayouts_old_sighting_is_a_from_price():
    row = {**load("travelpayouts_calendar.json")["data"][0], "found_at": "2026-09-10T04:00:00Z"}
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    deal = travelpayouts.map_row(row, "USD", SearchParams(), now)
    assert deal.price_confidence == "from_price" and deal.seen_at == "2026-09-10T04:00:00+00:00"
    fresh = travelpayouts.map_row({**row, "found_at": "2026-09-30T04:00:00+04:00"}, "USD", SearchParams(), now)
    assert fresh.price_confidence == "estimated" and fresh.seen_at == "2026-09-30T00:00:00+00:00"
    assert travelpayouts.map_row({**row, "price": 0}, "USD", SearchParams(), now) is None


def test_travelpayouts_search_sends_token_header_and_caches(temp_db, keys, http):
    calls, responses = http
    responses.append(load("travelpayouts_calendar.json"))
    query = FareQuery("TLV", "PRG", "2026-11", "", SearchParams())
    assert len(travelpayouts.search(query).deals) == 4
    assert calls[0]["headers"] == {"X-Access-Token": "tp-secret-token"}
    assert "token" not in calls[0]["params"]
    assert travelpayouts.search(query).cached and len(calls) == 1
    assert travelpayouts.used_this_month() == 1


def test_travelpayouts_failure_raises(temp_db, keys, http):
    _, responses = http
    responses.append({"success": False, "error": "Unauthorized", "data": None})
    with pytest.raises(ProviderError, match="Unauthorized"):
        travelpayouts.search(FareQuery("TLV", "PRG", "2026-11", "", SearchParams()))


def test_travelpayouts_link_party_rewrite():
    one_way = SearchParams(passengers=Passengers(adults=3))
    assert travelpayouts.booking_url("/search/MAD2807BCN1?t=x", one_way) == "https://www.aviasales.com/search/MAD2807BCN3?t=x"
    infant = SearchParams(passengers=Passengers(adults=2, infants=1))
    assert travelpayouts.booking_url("/search/MAD2807BCN26081?t=x", infant).startswith(
        "https://www.aviasales.com/search/MAD2807BCN2608201?t=x")
    assert travelpayouts.booking_url("/odd/path?t=x", infant) == "https://www.aviasales.com/odd/path?t=x"
