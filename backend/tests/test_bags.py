import json

import pytest

from app import bags, storage
from app.models import FlightDeal, Passengers, SearchParams

TABLE = {
    "as_of": "2026-10",
    "carriers": {
        "W6": {"name": "Wizz Air", "aliases": ["W4", "W9"], "currency": "EUR",
               "trolley": {"min": 10, "max": 40, "included": False},
               "checked": {"min": 30, "max": 50, "included": False, "kg": 20}},
        "TK": {"name": "Turkish Airlines", "currency": "EUR",
               "trolley": {"min": 0, "max": 0, "included": True},
               "checked": {"min": 0, "max": 0, "included": True, "kg": 20}},
        "FR": {"name": "Ryanair", "currency": "EUR",
               "trolley": {"min": 6, "max": 36, "included": False},
               "checked": {"min": None, "max": None, "included": False}},
    },
}


@pytest.fixture(autouse=True)
def fee_table(tmp_path, monkeypatch):
    path = tmp_path / "bag_fees.json"
    path.write_text(json.dumps(TABLE))
    monkeypatch.setattr(bags, "TABLE_PATH", path)
    monkeypatch.setattr(bags, "convert", lambda amount, src, dst: amount * (2 if (src, dst) == ("EUR", "ILS") else 1))
    bags.table.cache_clear()
    yield
    bags.table.cache_clear()


def deal(**kw) -> FlightDeal:
    base = dict(origin="TLV", destination="BUD", airline="Wizz Air", airline_code="W6",
                flight_numbers="W6 2328 / W6 2325", price=1000, currency="ILS",
                depart_date="2026-11-17", return_date="2026-11-24", source="google_flights")
    return FlightDeal(**{**base, **kw})


def family(**kw) -> SearchParams:
    return SearchParams(passengers=Passengers(adults=2, children=3), currency="ILS", **kw)


def test_no_bags_asked_is_the_fare():
    got = bags.price_with_bags(deal(), family())
    assert (got.basis, got.total, got.lines) == ("none", 1000, [])


def test_checked_bags_count_both_directions_from_the_table():
    got = bags.price_with_bags(deal(), family(checked_bags=2))
    [line] = got.lines
    # 2 bags x 2 directions x 30-50 EUR, at 2 ILS per EUR.
    assert (line.kind, line.count, line.directions, line.low, line.high, line.basis) == \
        ("checked", 2, 2, 240, 400, "table")
    assert (got.total_min, got.total, got.total_max) == (1240, 1320, 1400)
    assert got.basis == "table" and got.added_at_checkout and got.as_of == "2026-10"


def test_legacy_checked_flag_means_one_per_seated_traveller():
    params = family(checked_bag=True)
    assert params.checked_bags == 5
    one_way = bags.price_with_bags(deal(return_date=""), params)
    assert one_way.lines[0].low == 5 * 1 * 30 * 2


def test_google_already_priced_the_carry_on():
    got = bags.price_with_bags(deal(), family(cabin_bag=True, checked_bags=1))
    trolley, checked = got.lines
    assert (trolley.basis, trolley.high) == ("google", 0)
    assert checked.basis == "table" and got.basis == "table"


def test_trolley_from_the_table_when_the_source_didnt_add_it():
    got = bags.price_with_bags(deal(source="aviasales"), family(cabin_bag=True))
    [line] = got.lines
    assert (line.basis, line.low, line.high) == ("table", 5 * 2 * 10 * 2, 5 * 2 * 40 * 2)


def test_included_and_alias_and_unknown_carriers():
    tk = bags.price_with_bags(deal(airline="Turkish Airlines", airline_code="TK", flight_numbers="TK 785"),
                              family(checked_bags=5))
    assert tk.basis == "included" and tk.total == 1000 and not tk.added_at_checkout
    w9 = bags.price_with_bags(deal(airline="Wizz Air UK", airline_code="W9", flight_numbers="W9 1"), family(checked_bags=1))
    assert w9.lines[0].carrier == "Wizz Air"
    unknown = bags.price_with_bags(deal(airline="Nowhere Air", airline_code="ZZ", flight_numbers="ZZ 1"),
                                   family(checked_bags=1))
    assert unknown.basis == "typical" and unknown.lines[0].low == 25 * 2 * 2
    # A known carrier with no figure for this bag falls back to the typical range too.
    fr = bags.price_with_bags(deal(airline="Ryanair", airline_code="FR", flight_numbers="FR 1"), family(checked_bags=1))
    assert fr.basis == "typical"


def test_multi_carrier_trip_takes_the_dearer_fee():
    got = bags.price_with_bags(deal(airline="Turkish Airlines + Wizz Air", airline_code="",
                                    flight_numbers="TK 785 / W6 2325"), family(checked_bags=1))
    assert got.lines[0].carrier == "Wizz Air" and got.lines[0].high == 1 * 2 * 50 * 2


def test_llm_prices_are_left_as_quoted():
    got = bags.price_with_bags(deal(source="web"), family(checked_bags=2))
    assert (got.basis, got.total) == ("as_quoted", 1000)


def test_seller_fees_replace_the_table():
    priced = bags.price_with_bags(deal(), family(checked_bags=2))
    got = bags.with_seller_fees(priced, {"checked": 35.0})
    assert got.basis == "seller" and got.total == 1000 + 2 * 2 * 35


def test_stored_deals_carry_the_price_with_bags(temp_db):
    [stored] = storage.save_deals("TLV -> Budapest", [deal()], family(checked_bags=2))
    assert stored.bags.total == 1320 and stored.compare_total == 1320 and stored.compare_price == 1000
    again = storage.get_deal(stored.id)
    assert again.bags.total == 1320 and again.compare_total == 1320


def test_watch_runs_and_google_range_compare_with_bags():
    from datetime import datetime, timezone
    from app import alerts
    params = SearchParams(currency="EUR", checked_bags=1)
    row = {"found_at": "2026-10-01T06:00:00+00:00", "price": 100, "currency": "EUR", "source": "google_flights",
           "search_params": params.model_dump_json(), "airline": "Wizz Air", "airline_code": "W6",
           "flight_numbers": "W6 1 / W6 2", "return_date": "2026-11-24"}
    [run] = alerts.run_bests([row], "EUR", alerts.fare_kind(params))
    # 1 bag x 2 directions x 30-50 EUR -> +80 at the middle.
    assert (run.price, run.bags) == (180, 80)
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    google = alerts.GoogleHistory(150, 200, (), now)
    base = alerts.select_baseline([run], google)
    assert (base.google.typical_low, base.google.typical_high) == (230, 280)
    assert base.typical_text("EUR", now).endswith("per Google + bags")
