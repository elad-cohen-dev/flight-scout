"""A price you can check (app/pricecheck.py): Recheck re-runs a live fare with
every cache bypassed and stores the new sighting; the price-check log and its
per-airline summary; alert text with the price with bags. SerpApi answers are
fixtures through a faked providers.base.http_get_json (no network)."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import alerts, bags, main, pricecheck, storage
from app.alerts import WatchResult
from app.models import FlightDeal, Passengers, SearchParams, StoredDeal, Watch
from app.providers import base, serpapi
from app.providers.base import FareQuery

FIXTURES = Path(__file__).parent / "fixtures"
ONE_WAY = SearchParams(trip_type="one_way", currency="GBP")


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


@pytest.fixture
def client(temp_db, keys):
    return TestClient(main.app)


def stored_one_way(http) -> StoredDeal:
    """The cheapest fare of a live one-way search (Wizz W9 5702 at 131), saved
    like a search job does. Its response stays in our 1h cache."""
    calls, responses = http
    responses.append(load("serpapi_one_way.json"))
    result = serpapi.search(FareQuery("TLV", "LTN", "2026-11-12", "", ONE_WAY))
    saved = storage.save_deals("TLV -> London (November)", result.deals, ONE_WAY)
    calls.clear()
    return storage.get_deal(saved[0].id)


def repriced(price: float | None) -> dict:
    """The one-way fixture with the Wizz fare at `price` (None = gone)."""
    data = copy.deepcopy(load("serpapi_one_way.json"))
    for key in ("best_flights", "other_flights"):
        items = data.get(key) or []
        if price is None:
            data[key] = [it for it in items if it["flights"][0]["flight_number"] != "W9 5702"]
        else:
            for it in items:
                if it["flights"][0]["flight_number"] == "W9 5702":
                    it["price"] = price
    return data


# --- recheck -------------------------------------------------------------------

def test_recheck_spends_one_search_bypassing_the_cache_and_stores_the_sighting(client, http):
    calls, responses = http
    deal = stored_one_way(http)
    used = storage.provider_calls("serpapi")
    responses.append(repriced(120))

    res = client.post(f"/api/deals/{deal.id}/recheck")

    assert res.status_code == 200
    body = res.json()
    assert (body["status"], body["old"], body["new"], body["delta"]) == ("cheaper", 131, 120, -11)
    assert body["currency"] == "GBP" and body["old_seen_at"]
    # The identical request was in our cache, yet it went out again — with SerpApi's cache off too.
    assert len(calls) == 1 and calls[0]["no_cache"] == "true" and calls[0]["outbound_date"] == "2026-11-12"
    assert storage.provider_calls("serpapi") == used + 1
    new = body["deal"]
    assert new["id"] != deal.id and new["price"] == 120 and new["search_query"] == deal.search_query
    assert new["flight_numbers"] == "W9 5702"
    assert storage.get_deal(new["id"]).serp_params == deal.serp_params
    # The refreshed answer is cached under the plain request: a normal search reads it free.
    again = serpapi.search(FareQuery("TLV", "LTN", "2026-11-12", "", ONE_WAY))
    assert again.cached and again.deals[0].price == 120


def test_recheck_same_price_and_gone(client, http):
    _, responses = http
    deal = stored_one_way(http)
    responses.append(repriced(131))
    assert client.post(f"/api/deals/{deal.id}/recheck").json()["status"] == "same"

    before = len(storage.list_deals(dedupe=False))
    responses.append(repriced(None))
    body = client.post(f"/api/deals/{deal.id}/recheck").json()
    assert (body["status"], body["new"], body["delta"], body["deal"]) == ("gone", None, None, None)
    assert len(storage.list_deals(dedupe=False)) == before  # nothing stored


def test_recheck_refusals(client, http, monkeypatch):
    calls, _ = http
    deal = stored_one_way(http)
    web = storage.save_deals("TLV -> Rome", [FlightDeal(origin="TLV", destination="FCO", airline="X", price=100,
                                                        currency="USD", depart_date="2026-11-01", source="web")])[0]
    assert client.post(f"/api/deals/{web.id}/recheck").status_code == 400
    assert client.post("/api/deals/99999/recheck").status_code == 404

    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET", "0")
    res = client.post(f"/api/deals/{deal.id}/recheck")
    assert res.status_code == 409 and "budget" in res.json()["detail"]

    monkeypatch.delenv("SERPAPI_API_KEY")
    assert client.post(f"/api/deals/{deal.id}/recheck").status_code == 503
    assert calls == []


def test_match_needs_the_same_outbound():
    old = FlightDeal(origin="TLV", destination="PRG", airline="Smartwings", price=300, currency="USD",
                     depart_date="2026-11-05", depart_time="06:35", flight_numbers="QS 1287 / QS 1286")
    same = old.model_copy(update={"flight_numbers": "QS 1287", "price": 280})
    pricier_twin = same.model_copy(update={"price": 310})
    later = same.model_copy(update={"depart_time": "18:00"})
    other = same.model_copy(update={"flight_numbers": "LY 2521"})
    assert pricecheck.match(old, [other, pricier_twin, same, later]) is same
    assert pricecheck.match(old, [other, later]) is None


# --- price-check log -------------------------------------------------------------

def _deal(airline="Wizz Air", price=200.0, currency="EUR", params=None) -> StoredDeal:
    return storage.save_deals("TLV -> Budapest", [FlightDeal(
        origin="TLV", destination="BUD", airline=airline, price=price, currency=currency,
        depart_date="2026-11-01", return_date="2026-11-05", source="google_flights")], params)[0]


def test_price_checks_endpoint_and_summary(client):
    wizz = _deal(params=SearchParams(checked_bags=1))
    elal = _deal("El Al", 900, "ILS")
    post = lambda **kw: client.post("/api/price-checks", json=kw)  # noqa: E731

    res = post(deal_id=wizz.id, seller="Wizz Air", shown_total=280, same=True, currency="€")
    assert res.status_code == 201
    body = res.json()
    assert (body["actual_total"], body["same"], body["currency"], body["airline"]) == (280, True, "EUR", "Wizz Air")
    assert body["bag_basis"] == "typical"
    post(deal_id=wizz.id, shown_total=280, actual_total=295, same=False, currency="EUR")
    post(deal_id=wizz.id, shown_total=280, actual_total=310, same=False, currency="EUR")
    # A typed total within 1% is the same price, whatever was clicked.
    assert post(deal_id=elal.id, shown_total=900, actual_total=905, same=False, currency="ILS").json()["same"]
    post(deal_id=elal.id, shown_total=900, same=False, currency="ILS")  # "No", no total typed
    assert post(deal_id=99999, shown_total=1, same=True).status_code == 404
    assert post(deal_id=wizz.id, shown_total=0, same=True).status_code == 422

    s = client.get("/api/price-checks/summary").json()
    assert (s["checks"], s["same"]) == (5, 2)
    w, e = s["airlines"]
    assert (w["airline"], w["checks"], w["same"], w["same_pct"], w["median_diff"], w["currency"]) == \
        ("Wizz Air", 3, 1, 33, 15, "EUR")
    assert w["bags_estimated"] == 3
    assert (e["airline"], e["checks"], e["same"], e["median_diff"], e["bags_estimated"]) == ("El Al", 2, 1, 5, 0)


def test_summary_converts_into_the_airlines_main_currency():
    rows = [
        {"airline": "Ryanair", "seller": "", "currency": "EUR", "shown_total": 100, "actual_total": 110, "same": False},
        {"airline": "Ryanair", "seller": "", "currency": "EUR", "shown_total": 100, "actual_total": 100, "same": True},
        {"airline": "Ryanair", "seller": "", "currency": "USD", "shown_total": 100, "actual_total": None,
         "same": False},
    ]
    [r] = pricecheck.summarize(rows).airlines
    assert (r.checks, r.currency, r.median_diff) == (3, "EUR", 5)


# --- the watch editor's checked-bag count -----------------------------------------------

def test_watch_patch_sets_and_clears_the_checked_bag_count(client):
    created = client.post("/api/watches", json={
        "origin": "TLV", "destination": "Budapest", "dates_hint": "May", "max_price": 3000, "currency": "ILS",
        "passengers": {"adults": 2, "children": 2, "infants": 0}, "cabin_bag": False, "checked_bag": True,
    }).json()
    assert (created["checked_bag"], created["checked_bags"]) == (True, 4)  # legacy flag: one per seat
    wid = created["id"]
    patched = client.patch(f"/api/watches/{wid}", json={"checked_bags": 3, "checked_bag": True}).json()
    assert (patched["checked_bag"], patched["checked_bags"]) == (True, 3)
    cleared = client.patch(f"/api/watches/{wid}", json={"checked_bags": 0, "checked_bag": False}).json()
    assert (cleared["checked_bag"], cleared["checked_bags"]) == (False, 0)


# --- alerts: the price with bags ----------------------------------------------------

NOW = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)


def test_alert_text_shows_the_price_with_bags():
    params = SearchParams(passengers=Passengers(adults=2), checked_bags=2, currency="ILS")
    d = StoredDeal(id=1, search_query="q", found_at=NOW, origin="TLV", destination="BUD", airline="Wizz Air",
                   price=2140, currency="ILS", depart_date="2026-10-14", return_date="2026-10-18",
                   source="google_flights", search_params=params, price_per_person=1070)
    d.bags = bags.price_with_bags(d, params)
    w = Watch(id=1, origin="TLV", destination="Budapest", dates_hint="October", max_price=3000, currency="ILS",
              created_at=NOW, passengers=Passengers(adults=2), checked_bags=2)
    r = WatchResult(watch=w, best_price=d.bags.total, best=d,
                    hits=[alerts.RuleHit("threshold", "At or under your ₪3,000 target")])
    total = alerts.fmt_price(d.bags.total, "ILS")
    title, plain, rich = alerts.format_instant(r)
    assert title.startswith(f"TLV → Budapest: {total} with bags — ")
    assert f"{total} with 2 checked bags (fare ₪2,140) · Wizz Air" in plain
    assert "pp" not in plain  # the per-person figure is the bare fare's
    assert f"<b>{total} with 2 checked bags (fare ₪2,140)</b>" in rich

    # No bags asked: unchanged.
    plain_deal = d.model_copy(update={"bags": bags.price_with_bags(d, SearchParams(currency="ILS"))})
    assert alerts.bag_words(plain_deal) == ""
    # LLM prices quoted with bags: as quoted.
    assert alerts.bag_words(d.model_copy(update={"bags": d.bags.model_copy(update={"basis": "as_quoted"})})) == ""
