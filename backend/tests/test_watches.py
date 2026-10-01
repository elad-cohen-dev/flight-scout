"""Watch management: PATCH /api/watches/{id} (edit, pause/resume) and how paused
watches are treated by the daily run. Every agent search is faked."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import agent, main, storage, watcher
from app.models import FlightDeal, Passengers, WatchCreate


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    return TestClient(main.app)


@pytest.fixture
def searched(monkeypatch):
    """Fake agent: records which watches were searched, returns one $250 deal."""
    calls = []

    def fake_find_deals(origin, destination, dates_hint, *args, params=None, on_event=None, **kw):
        calls.append((destination, params))
        return [FlightDeal(origin=origin, destination=destination, airline="Wizz Air", price=250,
                           currency="USD", depart_date="2026-11-10", return_date="2026-11-14")]

    monkeypatch.setattr(agent, "find_deals", fake_find_deals)
    monkeypatch.setattr(watcher, "send_notification", lambda *a, **kw: [])
    return calls


def make_watch(**kw):
    base = dict(origin="TLV", destination="Athens", dates_hint="November", max_price=300,
                currency="USD", max_stops=0, passengers=Passengers(adults=2), cabin_bag=True)
    base.update(kw)
    return storage.add_watch(WatchCreate(**base))


def test_full_update(client):
    w = make_watch()
    res = client.patch(f"/api/watches/{w.id}", json={
        "dates_hint": "December", "max_price": 450, "currency": "eur", "trip_type": "one_way",
        "passengers": {"adults": 1, "children": 2}, "cabin_class": "business", "max_stops": 1,
        "cabin_bag": False, "checked_bag": True, "depart_from": "2026-12-01", "depart_to": "2026-12-10",
        "nearby_airports": True,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == w.id and (body["origin"], body["destination"]) == ("TLV", "Athens")
    assert (body["dates_hint"], body["max_price"], body["currency"]) == ("December", 450, "EUR")
    assert (body["trip_type"], body["cabin_class"], body["max_stops"], body["direct_only"]) == \
        ("one_way", "business", 1, False)
    assert body["passengers"] == {"adults": 1, "children": 2, "infants": 0}
    assert (body["cabin_bag"], body["checked_bag"], body["nearby_airports"]) == (False, True, True)
    assert (body["depart_from"], body["depart_to"]) == ("2026-12-01", "2026-12-10")
    # persisted, not just echoed
    [stored] = storage.list_watches()
    assert stored.model_dump(exclude={"credits"}) == main.Watch.model_validate(body).model_dump(exclude={"credits"})


def test_partial_update_keeps_other_fields(client):
    w = make_watch(depart_from=date(2026, 11, 1), nights_min=3)
    storage.update_watch_run(w.id, 320)
    res = client.patch(f"/api/watches/{w.id}", json={"max_price": 280})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["max_price"] == 280
    assert (body["dates_hint"], body["max_stops"], body["cabin_bag"], body["passengers"]["adults"]) == \
        ("November", 0, True, 2)
    assert (body["depart_from"], body["nights_min"]) == ("2026-11-01", 3)
    # only the threshold changed, so the last result still describes this search
    assert body["last_best_price"] == 320 and body["last_run_at"] is not None


def test_criteria_change_clears_last_result(client):
    w = make_watch()
    storage.update_watch_run(w.id, 320)
    body = client.patch(f"/api/watches/{w.id}", json={"cabin_class": "business"}).json()
    assert body["last_best_price"] is None and body["last_run_at"] is None


def test_null_clears_nullable_fields(client):
    w = make_watch(depart_from=date(2026, 11, 1), nights_min=3)
    body = client.patch(f"/api/watches/{w.id}", json={"max_stops": None, "depart_from": None, "nights_min": None}).json()
    assert (body["max_stops"], body["direct_only"], body["depart_from"], body["nights_min"]) == (None, False, None, None)


def test_direct_only_alias(client):
    w = make_watch(max_stops=None)
    assert client.patch(f"/api/watches/{w.id}", json={"direct_only": True}).json()["max_stops"] == 0
    body = client.patch(f"/api/watches/{w.id}", json={"direct_only": False}).json()
    assert (body["max_stops"], body["direct_only"]) == (None, False)
    # max_stops wins over the alias when both are sent
    body = client.patch(f"/api/watches/{w.id}", json={"direct_only": True, "max_stops": 1}).json()
    assert (body["max_stops"], body["direct_only"]) == (1, False)


def test_pause_resume_and_list_includes_paused(client):
    a, b = make_watch(), make_watch(destination="Rome")
    res = client.patch(f"/api/watches/{a.id}", json={"active": False})
    assert res.status_code == 200 and res.json()["active"] is False
    listed = {w["id"]: w["active"] for w in client.get("/api/watches").json()}
    assert listed == {a.id: False, b.id: True}
    assert [w.id for w in storage.list_watches(active_only=True)] == [b.id]
    # pausing doesn't touch criteria
    assert client.get("/api/watches").json()[0]["max_price"] == 300
    assert client.patch(f"/api/watches/{a.id}", json={"active": True}).json()["active"] is True


def test_paused_watch_excluded_from_runs(client, searched):
    a, b = make_watch(), make_watch(destination="Rome")
    client.patch(f"/api/watches/{a.id}", json={"active": False})
    summary = watcher.run_watches()
    assert summary["watches_run"] == 1 and [r["watch_id"] for r in summary["results"]] == [b.id]
    assert [dest for dest, _ in searched] == ["Rome"]
    assert watcher.run_watches(only_id=a.id)["watches_run"] == 0
    # "Run now" refuses a paused watch
    assert client.post(f"/api/watches/{a.id}/run").status_code == 404
    [paused] = [w for w in storage.list_watches() if w.id == a.id]
    assert paused.last_run_at is None


def test_edited_watch_history_keeps_watch_id(client, searched):
    w = make_watch()
    watcher.run_watches()
    client.patch(f"/api/watches/{w.id}", json={"cabin_class": "business", "dates_hint": "December"})
    watcher.run_watches()
    labels = sorted({d.search_query for d in storage.list_deals(dedupe=False)})
    assert len(labels) == 2 and all(label.endswith(f"[watch #{w.id}]") for label in labels)
    assert any("business" in label and "December" in label for label in labels)
    # the new criteria reach the agent
    assert searched[-1][1].cabin_class == "business"
    # the route's price history stays one series across the edit
    [history] = storage.get_route_history()
    assert history.route == "TLV -> Athens" and len(history.points) == 2


def test_patch_missing_watch_404(client):
    assert client.patch("/api/watches/999", json={"max_price": 100}).status_code == 404


@pytest.mark.parametrize("body", [
    {"max_price": 0},                       # threshold must be > 0 (only caught on merge)
    {"max_price": -5},
    {"max_price": None},                    # required field can't be nulled
    {"active": None},
    {"max_stops": 7},                       # out of SearchParams range
    {"nights_min": 99},
    {"passengers": {"adults": 0}},
    {"cabin_class": "luxury"},
    {"trip_type": "multi_city"},
    {"depart_from": "not-a-date"},
    {"origin": "HFA"},                      # the route isn't editable
    {"bogus": 1},
])
def test_patch_validation_errors(client, body):
    w = make_watch()
    res = client.patch(f"/api/watches/{w.id}", json=body)
    assert res.status_code == 422, res.text
    [stored] = storage.list_watches()
    assert stored.model_dump() == w.model_dump() | {"last_run_at": None}
