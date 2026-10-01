"""Deal details on demand (app/details.py): token persistence, the credit cost,
budget refusal, caching, expired tokens, the booking redirect page and the
endpoints' auth. Fixtures follow SerpApi's documented response shapes; every
request goes through a faked providers.base.http_get_json (no network)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import details, main, storage
from app.models import FlightDeal, FlightDealList, Passengers, SearchParams
from app.providers import base, serpapi
from app.providers.base import FareQuery

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "serp-secret-key")
    monkeypatch.delenv("APP_PASSWORD", raising=False)


@pytest.fixture
def http(monkeypatch):
    calls: list[dict] = []
    responses: list[dict] = []

    def fake(url, params, headers=None, *, provider):
        calls.append(dict(params))
        return responses.pop(0)

    monkeypatch.setattr(base, "http_get_json", fake)
    return calls, responses


ROUND = SearchParams(passengers=Passengers(adults=2), checked_bag=True)
ONE_WAY = SearchParams(trip_type="one_way")


def stored_round_trip(http) -> storage.StoredDeal:
    """The cheapest deal of a live round-trip search, saved like a search job does."""
    calls, responses = http
    responses.append(load("serpapi_round_trip.json"))
    result = serpapi.search(FareQuery("TLV", "PRG", "2026-11-05", "2026-11-09", ROUND))
    saved = storage.save_deals("TLV -> PRG", result.deals, ROUND)
    calls.clear()
    return storage.get_deal(saved[0].id)


def stored_one_way(http) -> storage.StoredDeal:
    calls, responses = http
    responses.append(load("serpapi_one_way.json"))
    result = serpapi.search(FareQuery("TLV", "LTN", "2026-11-12", "", ONE_WAY))
    saved = storage.save_deals("TLV -> London", result.deals, ONE_WAY)
    calls.clear()
    return storage.get_deal(saved[0].id)


# --- mapping -------------------------------------------------------------------

def test_parse_bag_wordings():
    lines = ["1 free carry-on", "1st checked bag: 99-187"]
    assert details.parse_bag(lines, "carry_on").fee == 0
    checked = details.parse_bag(lines, "checked")
    assert checked.fee is None and checked.text == "1st checked bag: 99-187"
    assert details.parse_bag(["1st checked bag: 1,035.50"], "checked").fee == 1035.5
    assert details.parse_bag(["No carry-on bag included"], "carry_on").fee is None
    assert details.parse_bag([], "checked").text == ""


def test_map_booking_options_together_and_separate_tickets():
    options = serpapi.map_booking_options(load("serpapi_booking_options.json"))
    assert len(options) == 3
    kiwi, smart, split = options
    assert kiwi["parts"][0]["book_with"] == "Kiwi.com" and not kiwi["parts"][0]["airline"]
    assert smart["parts"][0]["url"] == "https://www.google.com/travel/clk/f"
    assert smart["parts"][0]["post_data"].startswith("u=")
    assert smart["parts"][0]["phone"] == "+420 220 116 686"
    assert split["separate_tickets"] and [p["part"] for p in split["parts"]] == ["departing", "returning"]


def test_top_level_baggage_prices_fill_in_for_an_option():
    data = {"baggage_prices": {"together": ["1st checked bag: 30"]},
            "booking_options": [{"together": {"book_with": "X", "price": 100}}]}
    [option] = serpapi.map_booking_options(data)
    assert option["parts"][0]["baggage_prices"] == ["1st checked bag: 30"]


# --- token persistence -----------------------------------------------------------

def test_search_keeps_tokens_on_the_row_but_not_in_the_api_or_llm_schema(temp_db, keys, http):
    deal = stored_round_trip(http)
    assert deal.departure_token and not deal.booking_token
    assert json.loads(deal.serp_params)["departure_id"] == "TLV"
    assert "api_key" not in deal.serp_params
    assert deal.details_cost == 2
    one_way = stored_one_way(http)
    assert one_way.booking_token and one_way.details_cost == 1

    client = TestClient(main.app)
    body = client.get("/api/deals").text
    assert "departure_token" not in body and "booking_token" not in body and "serp_params" not in body
    assert '"details_cost":2' in body
    schema = json.dumps(FlightDealList.model_json_schema())
    assert "token" not in schema and "serp_params" not in schema


def test_non_google_deals_have_no_details(temp_db, keys):
    [deal] = storage.save_deals("x", [FlightDeal(origin="TLV", destination="PRG", airline="Wizz Air", price=90,
                                                  currency="USD", depart_date="2026-11-05", source="aviasales")])
    assert deal.details_cost == 0
    assert details.get_details(storage.get_deal(deal.id)).status == "unavailable"
    assert TestClient(main.app).post(f"/api/deals/{deal.id}/details").status_code == 404


# --- fetching --------------------------------------------------------------------

def test_round_trip_fetch_spends_two_then_caches(temp_db, keys, http):
    calls, responses = http
    deal = stored_round_trip(http)
    quote = details.get_details(deal)
    assert (quote.status, quote.cost, quote.left) == ("needs_fetch", 2, 229) and not calls

    responses += [load("serpapi_return_flights.json"), load("serpapi_booking_options.json")]
    got = details.fetch_details(deal)
    assert calls[0]["departure_token"] == deal.departure_token
    assert "departure_token" not in calls[1] and calls[1]["booking_token"].startswith("WyJDalJJWkZs")
    assert got.status == "ready" and got.spent == 2 and serpapi.remaining() == 227
    assert (got.return_leg.depart_time, got.return_leg.arrive_time, got.return_leg.flight_numbers) == \
        ("10:25", "15:20", "QS 1286")
    assert got.outbound.flight_numbers == "QS 1285"

    # Cheapest first; Smartwings sells direct with an exact 35 checked-bag fee, x2 travellers.
    names = [(s.book_with, s.price, s.airline) for s in got.sellers]
    assert names == [("Kiwi.com", 471, False), ("Smartwings", 486, True), ("Smartwings", 505, True)]
    kiwi, smart, split = got.sellers
    assert kiwi.checked.text == "1st checked bag: 42-60" and not kiwi.bags_verified
    assert smart.checked.fee == 35 and smart.carry_on.fee == 0
    assert smart.bags_verified and smart.price_incl_bags == 486 + 2 * 35
    assert split.separate_tickets and split.checked.fee == 40 and [l.part for l in split.links] == \
        ["departing", "returning"]
    assert got.verified_incl_bags and got.price_incl_bags == 556

    # The row learned the return's booking token and time: a re-fetch would cost 1.
    row = storage.get_deal(deal.id)
    assert row.booking_token and row.return_depart_time == "10:25" and row.details_cost == 1

    calls.clear()
    again = details.fetch_details(deal)
    assert again.status == "ready" and again.spent == 0 and not calls
    assert details.get_details(row).status == "ready" and serpapi.remaining() == 227


def test_one_way_fetch_costs_one(temp_db, keys, http):
    calls, responses = http
    deal = stored_one_way(http)
    assert details.get_details(deal).cost == 1
    responses.append(load("serpapi_booking_options.json"))
    got = details.fetch_details(deal)
    assert len(calls) == 1 and calls[0]["booking_token"] == deal.booking_token and got.spent == 1
    assert got.return_leg is None and got.bags_requested == []
    assert not got.verified_incl_bags  # no bags were asked for


def test_budget_refusal_spends_nothing(temp_db, keys, http, monkeypatch):
    calls, _ = http
    deal = stored_round_trip(http)
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "2")  # 1 used by the search -> 1 left, 2 needed
    quote = details.get_details(deal)
    assert quote.status == "needs_fetch" and "1 left" in quote.message
    with pytest.raises(details.DetailsError) as exc:
        details.fetch_details(deal)
    assert exc.value.status == 409 and not calls
    assert TestClient(main.app).post(f"/api/deals/{deal.id}/details").status_code == 409


def test_cached_return_leg_lowers_the_cost(temp_db, keys, http):
    _, responses = http
    deal = stored_round_trip(http)
    responses.append(load("serpapi_return_flights.json"))
    serpapi.return_flights(json.loads(deal.serp_params), deal.departure_token)
    assert details.cost(deal) == 1


def test_expired_booking_token_is_reported_and_cached(temp_db, keys, http):
    calls, responses = http
    deal = stored_one_way(http)
    responses.append({"search_metadata": {"status": "Success"},
                      "error": "Google Flights hasn't returned any results for this query."})
    got = details.fetch_details(deal)
    assert got.status == "expired" and "Search this route again" in got.message and got.spent == 1
    calls.clear()
    assert details.fetch_details(deal).status == "expired" and not calls
    assert details.get_details(deal).status == "expired"


def test_token_error_is_expired_but_other_errors_are_502(temp_db, keys, http):
    _, responses = http
    deal = stored_one_way(http)
    responses.append({"error": "Invalid booking_token."})
    assert details.fetch_details(deal).status == "expired"

    other = stored_round_trip(http)
    responses.append({"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"})
    with pytest.raises(details.DetailsError) as exc:
        details.fetch_details(other)
    assert exc.value.status == 502 and "serp-secret-key" not in exc.value.message


def test_unconfigured_server_says_so(temp_db, keys, http, monkeypatch):
    deal = stored_one_way(http)
    monkeypatch.delenv("SERPAPI_API_KEY")
    assert "isn't configured" in details.get_details(deal).message
    with pytest.raises(details.DetailsError) as exc:
        details.fetch_details(deal)
    assert exc.value.status == 503


# --- booking redirect --------------------------------------------------------------

def test_redirect_page_escapes_everything():
    evil = 'u=a"><script>alert(1)</script>&x=</form><img src=x onerror=alert(2)>'
    page = details.redirect_page('<b>Evil</b> "Air"', "https://www.google.com/travel/clk/f?a=1&b=\"2", evil, "n0nce")
    assert "<script>alert" not in page and "<img" not in page and "<b>Evil" not in page
    assert page.count("<script") == 1 and '<script nonce="n0nce">' in page
    assert 'action="https://www.google.com/travel/clk/f?a=1&amp;b=&quot;2"' in page
    fields = re.findall(r'<input type="hidden" name="([^"]*)" value="([^"]*)">', page)
    assert fields == [("u", 'a&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;'),
                      ("x", "&lt;/form&gt;&lt;img src=x onerror=alert(2)&gt;")]


def test_redirect_page_keeps_post_data_values_intact():
    page = details.redirect_page("Smartwings", "https://www.google.com/travel/clk/f", "u=EvsvCg%3D%3D&g=1", "n")
    assert '<input type="hidden" name="u" value="EvsvCg==">' in page  # the browser re-encodes on submit
    assert '<input type="hidden" name="g" value="1">' in page


def test_safe_target_only_allows_google_https():
    assert details.safe_target("https://www.google.com/travel/clk/f")
    for bad in ("http://www.google.com/travel/clk/f", "https://evil.com/?www.google.com",
                "https://www.google.com.evil.com/", "javascript:alert(1)", "https://googlecom.net/"):
        assert not details.safe_target(bad), bad


def _fetched_round_trip(http) -> tuple[storage.StoredDeal, details.DealDetails]:
    _, responses = http
    deal = stored_round_trip(http)
    responses += [load("serpapi_return_flights.json"), load("serpapi_booking_options.json")]
    return deal, details.fetch_details(deal)


def test_book_endpoint_serves_an_auto_submitting_form(temp_db, keys, http):
    deal, got = _fetched_round_trip(http)
    smart = got.sellers[1]
    res = TestClient(main.app).get(smart.links[0].url)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    assert 'action="https://www.google.com/travel/clk/f"' in res.text and 'method="post"' in res.text
    assert '<input type="hidden" name="g" value="1">' in res.text
    nonce = re.search(r"'nonce-([^']+)'", res.headers["content-security-policy"]).group(1)
    assert f'<script nonce="{nonce}">' in res.text and "form-action" not in res.headers["content-security-policy"]
    assert res.headers["cache-control"] == "no-store"
    # Separate tickets: each ticket has its own request.
    returning = got.sellers[2].links[1].url
    assert 'value="UkVUVVJOSU5H"' in TestClient(main.app).get(returning).text


def test_book_endpoint_refuses_unknown_options_and_non_google_targets(temp_db, keys, http, monkeypatch):
    deal, got = _fetched_round_trip(http)
    client = TestClient(main.app)
    assert client.get(f"/api/deals/{deal.id}/book/9").status_code == 410
    assert client.get(f"/api/deals/999/book/0").status_code == 410
    monkeypatch.setattr(details, "booking_request", lambda *a: ("X", "https://evil.example/", "u=1"))
    res = client.get(got.sellers[0].links[0].url)
    assert res.status_code == 502 and "evil.example" not in res.text


def test_endpoints_require_the_app_password(temp_db, keys, http, monkeypatch):
    deal, _ = _fetched_round_trip(http)
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    client = TestClient(main.app)
    auth = {"Authorization": "Bearer hunter2"}
    assert client.get(f"/api/deals/{deal.id}/details").status_code == 401
    assert client.post(f"/api/deals/{deal.id}/details").status_code == 401
    got = client.get(f"/api/deals/{deal.id}/details", headers=auth).json()
    assert got["status"] == "ready" and "post_data" not in json.dumps(got)

    link = got["sellers"][1]["links"][0]["url"]
    q = {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}
    assert q["sig"] and q["part"] == "together"
    assert client.get(link).status_code == 200  # signed: works as a plain browser link
    bare = urlsplit(link).path
    assert client.get(bare).status_code == 401
    assert client.get(bare, headers=auth).status_code == 200
    tampered = link.replace(f"sig={q['sig']}", "sig=" + "0" * 32)
    assert client.get(tampered).status_code == 401
    other_option = link.replace(f"/book/{bare.rsplit('/', 1)[1]}", "/book/0")
    assert client.get(other_option).status_code == 401  # the signature covers the option
    assert not details.book_link_valid(deal.id, 1, "together", int(q["exp"]), q["sig"], now=int(q["exp"]) + 1)


def test_search_time_return_leg_keeps_the_booking_token(temp_db, keys, http):
    _, responses = http
    responses += [load("serpapi_round_trip.json"), load("serpapi_return_flights.json")]
    query = FareQuery("TLV", "PRG", "2026-11-05", "2026-11-09", ROUND)
    result = serpapi.search(query)
    deal, _ = serpapi.with_return_leg(result.deals[0], query, result.departure_tokens[0])
    assert deal.booking_token.startswith("WyJDalJJWkZs") and deal.departure_token
    [saved] = storage.save_deals("q", [deal], ROUND)
    assert saved.details_cost == 1
