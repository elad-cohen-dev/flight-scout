"""Trips (3.0): storage, resolution (app/trips.py), the REST API and the bot's
/trips command. Offline: temp SQLite DB, static currency rates, no bag table."""

import time

import pytest
from fastapi.testclient import TestClient

from app import main, storage, telegram_bot, trips, watcher
from app.models import (
    BookedFlight, FlightDeal, Passengers, SearchParams, TripCreate, TripItemCreate, WatchCreate, search_label,
)


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return TestClient(main.app)


def deal(price, airline="Wizz Air", currency="USD", depart="2026-12-20", ret="2026-12-27"):
    return FlightDeal(origin="TLV", destination="Rome", airline=airline, price=price, currency=currency,
                      depart_date=depart, return_date=ret, origin_airport="TLV", destination_airport="FCO",
                      source="google_flights", price_confidence="verified")


def label(params=None):
    return search_label("TLV", "Rome", "December", False, False, False, params=params)


# --- models ------------------------------------------------------------------------

def test_item_create_validation():
    item = TripItemCreate(kind="search", search_query="TLV -> Rome (December) [watch #4]", ref_id=9)
    assert (item.search_query, item.ref_id) == ("TLV -> Rome (December)", None)
    with pytest.raises(ValueError):
        TripItemCreate(kind="search")
    with pytest.raises(ValueError):
        TripItemCreate(kind="deal")
    assert TripItemCreate(kind="watch", ref_id=3, search_query="x").search_query == ""
    with pytest.raises(ValueError):
        TripCreate(name="   ")
    assert TripCreate(name=" Rome ", currency="₪").currency == "ILS"


# --- storage -----------------------------------------------------------------------

def test_trip_crud_and_items(temp_db):
    t = storage.add_trip(TripCreate(name="Rome Hanukkah", origin="TLV", destination="Rome",
                                    passengers=Passengers(adults=2, children=2), currency="ILS"))
    assert (t.id, t.status, t.passengers.children, t.items) == (1, "planning", 2, 0)
    [stored] = storage.list_trips()
    assert stored.name == "Rome Hanukkah" and stored.currency == "ILS"

    [d] = storage.save_deals(label(), [deal(300)])
    a = storage.add_trip_item(t.id, TripItemCreate(kind="deal", ref_id=d.id, note="nice times"))
    again = storage.add_trip_item(t.id, TripItemCreate(kind="deal", ref_id=d.id))
    assert again.id == a.id  # no duplicate links
    storage.add_trip_item(t.id, TripItemCreate(kind="search", search_query=label()))
    assert storage.get_trip(t.id).items == 2

    pinned = storage.update_trip_item(t.id, a.id, pinned=True)
    assert pinned.pinned and pinned.note == "nice times"
    assert storage.update_trip_item(t.id + 1, a.id, pinned=False) is None  # other trip's item

    updated = storage.update_trip(t.id, TripCreate(**{**t.model_dump(include=set(TripCreate.model_fields)),
                                                      "notes": "kids off school"}))
    assert updated.notes == "kids off school"

    assert storage.delete_trip_item(t.id, a.id)
    assert not storage.delete_trip_item(t.id, a.id)
    assert storage.get_trip(t.id).items == 1


def test_delete_trip_keeps_deals_and_watches(temp_db):
    t = storage.add_trip(TripCreate(name="x"))
    [d] = storage.save_deals(label(), [deal(300)])
    w = storage.add_watch(WatchCreate(origin="TLV", destination="Rome", max_price=300))
    storage.add_trip_item(t.id, TripItemCreate(kind="deal", ref_id=d.id))
    storage.add_trip_item(t.id, TripItemCreate(kind="watch", ref_id=w.id))
    assert storage.delete_trip(t.id)
    assert storage.list_trips() == [] and storage.list_trip_items() == []
    assert storage.get_deal(d.id) is not None and storage.get_watch(w.id) is not None
    assert not storage.delete_trip(t.id)


def test_search_runs_latest_run_and_watch_runs(temp_db, monkeypatch):
    plain = label()
    storage.save_deals(plain, [deal(400), deal(380, airline="El Al")])
    storage.save_deals("TLV -> Rome (December 50% off_sale)", [deal(10)])  # LIKE wildcards must not match
    w = storage.add_watch(WatchCreate(origin="TLV", destination="Rome", dates_hint="December", max_price=300))
    time.sleep(0.01)  # a later run of the same search, by a watch
    storage.save_deals(plain + f" [watch #{w.id}]", [deal(350)])
    latest, runs = storage.search_runs(plain)
    assert runs == 2 and [d.price for d in latest] == [350]
    assert storage.search_runs("nothing here") == ([], 0)


def test_set_booked_round_trip(temp_db):
    t = storage.add_trip(TripCreate(name="x"))
    booked = storage.set_trip_booked(t.id, BookedFlight(airline="El Al", price=4200, currency="ILS",
                                                        booking_ref="ABC123"), "booked")
    assert booked.status == "booked" and booked.booked.booking_ref == "ABC123"
    cleared = storage.set_trip_booked(t.id, None, "planning")
    assert cleared.booked is None and cleared.status == "planning"
    assert storage.set_trip_booked(99, None, "planning") is None


# --- resolution (app/trips.py) ------------------------------------------------------

def test_best_price_uses_bags_and_trip_currency(temp_db):
    params = SearchParams(checked_bag=True, checked_bags=2, currency="USD")
    stored = storage.save_deals(label(params), [deal(300), deal(320, airline="El Al")], params)
    wizz, elal = stored
    # bags are priced on read: the bare fare isn't what ranks
    assert wizz.compare_total > wizz.price
    t = storage.add_trip(TripCreate(name="Rome", currency="ILS"))
    storage.add_trip_item(t.id, TripItemCreate(kind="search", search_query=label(params)))
    [summary] = trips.with_best(storage.list_trips())
    cheapest = min(stored, key=lambda d: d.compare_total)
    assert summary.best.currency == "ILS" and summary.best.kind == "search"
    assert summary.best.deal_id == cheapest.id
    assert summary.best.price > cheapest.compare_total  # converted USD -> ILS


def test_detail_resolves_everything(temp_db):
    [s1, s2] = storage.save_deals(label(), [deal(500), deal(300, airline="El Al")])
    [shortlisted] = storage.save_deals("TLV -> Rome (January)", [deal(200, currency="EUR")])
    w = storage.add_watch(WatchCreate(origin="TLV", destination="Rome", dates_hint="winter", max_price=300))
    storage.save_deals(watcher.watch_label(w), [deal(280)], watcher.watch_params(w))
    storage.update_watch_run(w.id, 280)
    t = storage.add_trip(TripCreate(name="Rome"))
    storage.add_trip_item(t.id, TripItemCreate(kind="search", search_query=label()))
    storage.add_trip_item(t.id, TripItemCreate(kind="watch", ref_id=w.id))
    storage.add_trip_item(t.id, TripItemCreate(kind="deal", ref_id=s1.id))
    pin = storage.add_trip_item(t.id, TripItemCreate(kind="deal", ref_id=shortlisted.id, pinned=True))
    storage.add_trip_item(t.id, TripItemCreate(kind="watch", ref_id=999))  # a deleted watch
    storage.set_trip_booked(t.id, BookedFlight(deal_id=s2.id), "booked")

    d = trips.detail(storage.get_trip(t.id))
    [search] = d.searches
    assert search.route == "TLV -> Rome" and search.runs == 1
    assert [x.airline for x in search.deals] == ["El Al", "Wizz Air"]  # cheapest first
    assert d.watches[0].watch.id == w.id and d.watches[0].watch.stats is not None
    assert d.watches[1].watch is None
    assert d.deals[0].item.id == pin.id  # pinned first
    assert d.booked_deal.id == s2.id
    assert (d.best.kind, d.best.deal_id, d.best.currency) == ("deal", shortlisted.id, "USD")  # €200 < $280 < $300


# --- API ---------------------------------------------------------------------------

def test_api_crud(client):
    r = client.post("/api/trips", json={"name": "Sukkot Greece", "origin": "TLV", "destination": "Athens",
                                        "dates_hint": "Sukkot", "passengers": {"adults": 2, "children": 1}})
    assert r.status_code == 201
    trip = r.json()
    assert trip["status"] == "planning" and trip["best"] is None
    assert client.post("/api/trips", json={"name": ""}).status_code == 422

    r = client.patch(f"/api/trips/{trip['id']}", json={"notes": "ask grandma", "depart_from": "2026-10-05"})
    assert r.status_code == 200 and r.json()["notes"] == "ask grandma" and r.json()["depart_from"] == "2026-10-05"
    assert client.patch(f"/api/trips/{trip['id']}", json={"name": None}).status_code == 422
    assert client.patch(f"/api/trips/{trip['id']}", json={"bogus": 1}).status_code == 422
    assert client.patch("/api/trips/99", json={"notes": "x"}).status_code == 404

    [listed] = client.get("/api/trips").json()
    assert listed["name"] == "Sukkot Greece"
    assert client.get("/api/trips/99").status_code == 404
    assert client.delete(f"/api/trips/{trip['id']}").status_code == 204
    assert client.delete(f"/api/trips/{trip['id']}").status_code == 404


def test_api_items_and_detail(client):
    [d] = storage.save_deals(label(), [deal(300)])
    w = storage.add_watch(WatchCreate(origin="TLV", destination="Rome", max_price=300))
    tid = client.post("/api/trips", json={"name": "Rome"}).json()["id"]

    assert client.post(f"/api/trips/{tid}/items", json={"kind": "deal", "ref_id": 999}).status_code == 404
    assert client.post(f"/api/trips/{tid}/items", json={"kind": "watch", "ref_id": 999}).status_code == 404
    assert client.post(f"/api/trips/{tid}/items", json={"kind": "deal"}).status_code == 422
    assert client.post("/api/trips/99/items", json={"kind": "deal", "ref_id": d.id}).status_code == 404

    item = client.post(f"/api/trips/{tid}/items", json={"kind": "deal", "ref_id": d.id}).json()
    client.post(f"/api/trips/{tid}/items", json={"kind": "watch", "ref_id": w.id})
    client.post(f"/api/trips/{tid}/items", json={"kind": "search", "search_query": label()})
    r = client.patch(f"/api/trips/{tid}/items/{item['id']}", json={"note": "best times", "pinned": True})
    assert r.json()["pinned"] is True and r.json()["note"] == "best times"

    detail = client.get(f"/api/trips/{tid}").json()
    assert detail["items"] == 3
    assert detail["deals"][0]["deal"]["id"] == d.id and "bags" in detail["deals"][0]["deal"]
    assert detail["deals"][0]["deal"]["compare_total"] is not None
    assert detail["searches"][0]["deals"][0]["id"] == d.id
    assert detail["watches"][0]["watch"]["id"] == w.id
    assert detail["best"]["price"] == pytest.approx(d.compare_total)

    assert client.delete(f"/api/trips/{tid}/items/{item['id']}").status_code == 204
    assert client.delete(f"/api/trips/{tid}/items/{item['id']}").status_code == 404
    assert storage.get_deal(d.id) is not None  # unlinking never deletes


def test_api_mark_booked_from_deal_and_free_form(client):
    params = SearchParams(checked_bag=True, checked_bags=1)
    [d] = storage.save_deals(label(params), [deal(300)], params)
    tid = client.post("/api/trips", json={"name": "Rome"}).json()["id"]

    assert client.put(f"/api/trips/{tid}/booked", json={"deal_id": 999}).status_code == 404
    r = client.put(f"/api/trips/{tid}/booked", json={"deal_id": d.id, "booking_ref": "XYZ"})
    trip = r.json()
    assert trip["status"] == "booked"
    booked = trip["booked"]
    assert (booked["airline"], booked["depart_date"], booked["booking_ref"]) == ("Wizz Air", "2026-12-20", "XYZ")
    assert booked["price"] == pytest.approx(d.bags.total) and booked["price"] > 300  # paid incl. the bag
    assert booked["booked_at"]
    assert client.get(f"/api/trips/{tid}").json()["booked_deal"]["id"] == d.id

    r = client.put(f"/api/trips/{tid}/booked", json={"airline": "Arkia", "flight_numbers": "IZ 211",
                                                     "price": 3900, "currency": "₪"})
    assert r.json()["booked"]["currency"] == "ILS" and r.json()["booked"]["deal_id"] is None

    client.patch(f"/api/trips/{tid}", json={"status": "done"})
    assert client.put(f"/api/trips/{tid}/booked", json={"airline": "Arkia"}).json()["status"] == "done"
    client.patch(f"/api/trips/{tid}", json={"status": "booked"})
    cleared = client.delete(f"/api/trips/{tid}/booked").json()
    assert cleared["booked"] is None and cleared["status"] == "planning"


# --- Telegram /trips ---------------------------------------------------------------

def test_format_trips(temp_db):
    assert "No trips yet" in telegram_bot.format_trips([])
    [d] = storage.save_deals(label(), [deal(300)])
    t = storage.add_trip(TripCreate(name="Rome <3", origin="TLV", destination="Rome", dates_hint="Hanukkah"))
    storage.add_trip_item(t.id, TripItemCreate(kind="deal", ref_id=d.id))
    storage.add_trip(TripCreate(name="Empty"))
    booked = storage.add_trip(TripCreate(name="Paris"))
    storage.set_trip_booked(booked.id, BookedFlight(airline="El Al", flight_numbers="LY 323", price=4000,
                                                    currency="ILS"), "booked")
    text = telegram_bot.format_trips(trips.with_best(storage.list_trips()))
    assert "<b>Rome &lt;3</b> · 🗺 planning" in text
    assert "TLV → Rome · Hanukkah · best $" in text and "incl. bags" in text
    assert "nothing linked yet" in text
    assert "✅ booked" in text and "paid ₪4,000 · El Al LY 323" in text


def test_trips_command(temp_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")
    storage.add_trip(TripCreate(name="Rome"))
    reply = telegram_bot.handle_update(
        {"message": {"chat": {"id": 4242}, "text": "/trips"}}, run_watch=lambda *a: None)
    assert "<b>Rome</b>" in reply["text"]
    assert any(c["command"] == "trips" for c in telegram_bot.COMMANDS)
