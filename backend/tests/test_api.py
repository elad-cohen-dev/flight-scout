import time

from fastapi.testclient import TestClient

from app import agent, main
from app.models import FlightDeal


def test_search_passes_params_and_saves(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    seen = {}

    def fake_find_deals(origin, destination, dates_hint, *args, params=None, on_event=None, **kw):
        seen["params"] = params
        return [FlightDeal(origin=origin, destination=destination, airline="Wizz Air", price=500,
                           currency="EUR", depart_date="2026-11-10", return_date="")]

    monkeypatch.setattr(agent, "find_deals", fake_find_deals)
    client = TestClient(main.app)
    body = {"origin": "TLV", "destination": "Rome", "dates_hint": "November", "trip_type": "one_way",
            "passengers": {"adults": 2, "children": 1}, "cabin_class": "business", "max_stops": 1}
    job = client.post("/api/search", json=body).json()
    assert job["query"] == "TLV -> Rome (November, one-way, 2 adults + 1 child, business, max 1 stop)"
    for _ in range(100):
        job = client.get(f"/api/jobs/{job['job_id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "done", job
    assert seen["params"].passengers.total == 3 and seen["params"].trip_type == "one_way"
    [deal] = client.get("/api/deals").json()
    assert deal["search_params"]["cabin_class"] == "business"
    assert isinstance(deal["booking_links"], list)


def test_watch_api_round_trip(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    client = TestClient(main.app)
    created = client.post("/api/watches", json={
        "origin": "TLV", "destination": "Athens", "max_price": 300, "currency": "USD",
        "trip_type": "one_way", "passengers": {"adults": 2}, "max_stops": 0,
    })
    assert created.status_code == 201
    [w] = client.get("/api/watches").json()
    assert (w["trip_type"], w["passengers"]["adults"], w["max_stops"], w["direct_only"]) == \
        ("one_way", 2, 0, True)
