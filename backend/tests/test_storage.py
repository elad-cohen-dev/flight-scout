import sqlite3
from datetime import date

import pytest

from app import booking, storage, watcher
from app.models import BookingLink, FlightDeal, Passengers, SearchParams, WatchCreate


def deal(**kw) -> FlightDeal:
    base = dict(origin="Tel Aviv", destination="London", airline="Wizz Air", price=420,
                currency="EUR", depart_date="2026-10-14", return_date="2026-10-18", stops=0,
                origin_airport="TLV", destination_airport="LTN", depart_time="06:10",
                arrive_time="09:45", return_depart_time="10:30", flight_numbers="W6 2327 / W6 2328",
                airline_code="W6", price_per_person=210, price_confidence="verified",
                booking_url="https://wizzair.com/")
    base.update(kw)
    return FlightDeal(**base)


PARAMS = SearchParams(passengers=Passengers(adults=2), cabin_class="economy", max_stops=0,
                      depart_from=date(2026, 10, 10), currency="EUR", checked_bag=True)


def test_round_trip_new_fields(temp_db):
    saved = storage.save_deals("TLV -> London (October)", [deal()], PARAMS)
    assert saved[0].search_params == PARAMS
    [row] = storage.list_deals()
    assert row.id == saved[0].id
    for field in ("origin_airport", "destination_airport", "depart_time", "arrive_time",
                  "return_depart_time", "flight_numbers", "airline_code", "price_per_person",
                  "price_confidence"):
        assert getattr(row, field) == getattr(deal(), field), field
    assert row.search_params == PARAMS
    assert isinstance(row.booking_links, list)


def test_booking_links_populated_and_failure_tolerated(temp_db, monkeypatch):
    calls = []

    def fake(d, params):
        calls.append(params)
        return [BookingLink(label="X", url="https://x.test", kind="other")]

    monkeypatch.setattr(booking, "build_links", fake)
    storage.save_deals("q (a)", [deal()], PARAMS)
    storage.save_deals("q (b)", [deal(price=1)], None)
    deals = storage.list_deals()
    assert all(d.booking_links[0].url == "https://x.test" for d in deals)
    assert PARAMS in calls and None in calls

    def boom(d, params):
        raise RuntimeError("booking bug")

    monkeypatch.setattr(booking, "build_links", boom)
    storage._invalidate_cache()
    assert all(d.booking_links == [] for d in storage.list_deals())


def test_dedupe_separates_parties(temp_db):
    storage.save_deals("q (a)", [deal()], PARAMS)
    storage.save_deals("q (b)", [deal()], PARAMS)
    storage.save_deals("q (c)", [deal()], PARAMS.model_copy(update={"passengers": Passengers(adults=3)}))
    deals = storage.list_deals()
    assert sorted(d.times_seen for d in deals) == [1, 2]
    assert len(storage.list_deals(dedupe=False)) == 3


def test_watch_round_trip(temp_db):
    created = storage.add_watch(WatchCreate(
        origin="TLV", destination="Athens", dates_hint="November", max_price=400, currency="USD",
        trip_type="one_way", passengers=Passengers(adults=2, infants=1), cabin_class="business",
        max_stops=1, nearby_airports=True, depart_from=date(2026, 11, 1), nights_min=2,
    ))
    [w] = storage.list_watches()
    assert w.id == created.id
    assert (w.trip_type, w.cabin_class, w.max_stops, w.nearby_airports) == ("one_way", "business", 1, True)
    assert w.passengers == Passengers(adults=2, infants=1)
    assert w.depart_from == date(2026, 11, 1) and w.max_price == 400 and not w.direct_only
    params = watcher.watch_params(w)
    assert params.max_price is None and params.cabin_class == "business"
    assert watcher.watch_label(w).endswith(f"[watch #{w.id}]")
    assert "up to" not in watcher.watch_label(w)  # the threshold is not a search filter


def test_best_link():
    d = storage.StoredDeal(id=1, search_query="q", found_at="2026-09-30T00:00:00+00:00",
                           **deal(booking_url="https://fallback.test").model_dump())
    assert watcher.best_link(d) == "https://fallback.test"
    d.booking_links = [
        BookingLink(label="GF", url="https://gf.test", kind="google_flights", prefilled=["route", "dates"]),
        BookingLink(label="W6", url="https://w6.test", kind="airline", prefilled=["route", "dates"]),
        BookingLink(label="Sky", url="https://sky.test", kind="skyscanner", prefilled=["route"]),
    ]
    assert watcher.best_link(d) == "https://w6.test"


OLD_SCHEMA = [
    """CREATE TABLE deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, search_query TEXT NOT NULL, origin TEXT NOT NULL,
        destination TEXT NOT NULL, airline TEXT NOT NULL, price REAL NOT NULL, currency TEXT NOT NULL,
        depart_date TEXT NOT NULL, return_date TEXT NOT NULL DEFAULT '', duration TEXT NOT NULL DEFAULT '',
        stops INTEGER NOT NULL DEFAULT 0, booking_url TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', found_at TEXT NOT NULL)""",
    """CREATE TABLE watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL, destination TEXT NOT NULL,
        dates_hint TEXT NOT NULL, max_price REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'USD',
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_run_at TEXT,
        last_best_price REAL)""",
]


def test_migrates_old_schema(temp_db):
    conn = sqlite3.connect(temp_db)
    for stmt in OLD_SCHEMA:
        conn.execute(stmt)
    conn.execute("""INSERT INTO deals (search_query, origin, destination, airline, price, currency,
                    depart_date, found_at) VALUES ('TLV -> Rome (May, direct only)', 'TLV', 'Rome',
                    'ITA', 199, '₪', '2026-05-02', '2026-08-01T00:00:00+00:00')""")
    conn.execute("""INSERT INTO watches (origin, destination, dates_hint, max_price, created_at)
                    VALUES ('TLV', 'Rome', 'May', 300, '2026-08-01T00:00:00+00:00')""")
    conn.commit()
    conn.close()

    [legacy] = storage.list_deals()
    assert legacy.currency == "ILS" and legacy.search_params is None
    assert legacy.price_confidence == "estimated" and legacy.origin_airport == ""
    assert legacy.price_per_person is None
    [w] = storage.list_watches()
    assert (w.trip_type, w.cabin_class, w.max_stops, w.direct_only) == ("round_trip", "economy", None, False)

    storage.save_deals("TLV -> Rome (May)", [deal()], PARAMS)
    assert len(storage.list_deals()) == 2

    # Re-running init on an already-migrated DB is harmless.
    storage.reset_state()
    assert len(storage.list_deals()) == 2


def test_legacy_row_links_honour_label_flags():
    from app.models import StoredDeal
    from app.storage import _legacy_params

    deal = StoredDeal(
        id=1, search_query="TLV -> London (October, direct only, checked 20kg bag) [watch #13]",
        found_at="2026-09-01T00:00:00", origin="TLV", destination="London", airline="Wizz Air",
        price=200, currency="EUR", depart_date="2026-10-22", return_date="2026-10-28",
    )
    params = _legacy_params(deal)
    assert params.max_stops == 0 and params.checked_bag and not params.cabin_bag
    assert params.trip_type == "round_trip" and params.currency == "EUR"


def test_compare_price_ranks_across_currencies(monkeypatch):
    from app import storage
    from app.models import SearchParams, StoredDeal

    monkeypatch.setattr(storage, "_convert_currency",
                        lambda amount, src, dst: amount if src == dst else {"ILS": amount / 3.7, "EUR": amount * 1.16}.get(src))
    def deal(price, ccy, params):
        return StoredDeal(id=1, search_query="TLV -> Prague (October)", found_at="2026-09-30T00:00:00",
                          origin="TLV", destination="PRG", airline="x", price=price, currency=ccy,
                          depart_date="2026-10-12", search_params=params)
    usd = SearchParams(currency="USD")
    deals = [deal(600, "USD", usd), deal(2123, "ILS", usd), deal(681, "EUR", usd), deal(10, "XXX", usd)]
    for d in deals:
        storage._set_compare_price(d)
    assert [d.compare_price for d in deals] == [600, 573.78, 789.96, None]
    assert all(d.compare_currency == "USD" for d in deals)
    legacy = deal(100, "EUR", None)
    storage._set_compare_price(legacy)
    assert legacy.compare_currency == "USD" and legacy.compare_price == 116
