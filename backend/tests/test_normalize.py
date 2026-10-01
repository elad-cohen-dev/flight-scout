import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from app import airlines, airports, storage
from app.agent import postprocess
from app.models import FlightDeal, Passengers, SearchParams
from app.normalize import FLAGS, normalize_date, normalize_deal, normalize_deals, normalize_time

TODAY = date(2026, 10, 1)
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def deal(**kw) -> FlightDeal:
    base = dict(origin="Tel Aviv", destination="Prague", airline="Wizz Air", price=300,
                currency="EUR", depart_date="2026-10-14", return_date="2026-10-18", stops=0)
    base.update(kw)
    return FlightDeal(**base)


def norm(**kw):
    return normalize_deal(deal(**kw), None, TODAY)


# --- dataset ------------------------------------------------------------------

def test_airport_json_matches_frontend_dataset():
    exporter = _script("export_airports")
    assert exporter.TARGET.read_text(encoding="utf-8") == exporter.render(), \
        "run: cd backend && uv run python scripts/export_airports.py"
    data = json.loads(exporter.render())
    assert len(data["airports"]) > 500 and len(data["metros"]) > 20
    assert all(a["country"] in data["countries"] for a in data["airports"])


def test_every_dataset_country_has_hub_and_currency():
    assert set(airports.index().countries) <= set(airports.COUNTRY_INFO)


def test_airline_table_is_consistent():
    assert len(airlines._CARRIERS) >= 80
    codes = [c for codes, _, _ in airlines._CARRIERS for c in codes]
    assert len(codes) == len(set(codes)), "a code is listed twice"
    assert all(len(c) == 2 for c in codes)


# --- airlines -------------------------------------------------------------------

@pytest.mark.parametrize("name,code,flights,expected", [
    ("EL AL", "", "", ("El Al", "LY")),
    ("El Al", "", "", ("El Al", "LY")),
    ("El Al Israel Airlines", "", "", ("El Al", "LY")),
    ("Israir Airlines", "", "", ("Israir", "6H")),
    ("Wizz Air UK", "", "", ("Wizz Air", "W9")),
    ("Wizz Air", "", "W9 5301", ("Wizz Air", "W9")),
    ("wizzair", "W6", "", ("Wizz Air", "W6")),
    ("", "", "LY 315", ("El Al", "LY")),
    ("Arkia + Israir", "", "", ("Arkia + Israir", "IZ")),
    ("Aegean/Olympic", "", "", ("Aegean Airlines + Olympic Air", "A3")),
    ("multiple", "", "", ("Multiple airlines", "")),
    ("Smartwings (QS)", "", "", ("Smartwings", "QS")),
    ("ITA", "", "", ("ITA Airways", "AZ")),
])
def test_airline_resolution(name, code, flights, expected):
    assert airlines.resolve(name, code, flights)[:2] == expected


def test_airline_identity_merges_spellings_and_subsidiaries():
    assert airlines.identity("EL AL") == airlines.identity("El Al") == airlines.identity("", "LY") == "LY"
    assert airlines.identity("Wizz Air UK") == airlines.identity("Wizz Air", "W6") == "W6"
    assert airlines.identity("Some Charter") == "some charter"


def test_airline_flags():
    d, flags = norm(airline="EL AL")
    assert (d.airline, d.airline_code) == ("El Al", "LY") and "airline_unrecognized" not in flags
    d, flags = norm(airline="Sky Charter Co")
    assert d.airline == "Sky Charter Co" and "airline_unrecognized" in flags
    assert "airline_missing" in norm(airline="")[1]
    assert "airline_code_invalid" in norm(airline="El Al", airline_code="ELAL")[1]
    assert "flight_airline_mismatch" in norm(airline="El Al", flight_numbers="W6 2328")[1]


@pytest.mark.parametrize("raw,expected,bad", [
    ("W62328", "W6 2328", []),
    ("w6-2327, W6 2328", "W6 2327 / W6 2328", []),
    ("LY0315 and LY 316", "LY 315 / LY 316", []),
    ("unknown", "", []),
    ("2328", "2328", ["2328"]),
    ("", "", []),
])
def test_flight_numbers(raw, expected, bad):
    assert airlines.normalize_flight_numbers(raw) == (expected, bad)


# --- airports -----------------------------------------------------------------

@pytest.mark.parametrize("text,code", [
    ("TLV", "TLV"), ("tel aviv", "TLV"), ("Tel Aviv (TLV)", "TLV"), ("Prague [PRG]", "PRG"),
    ("Ben Gurion", "TLV"), ("תל אביב", "TLV"), ("London", "LON"), ("London Luton", "LTN"),
    ("Luton", "LTN"), ("Paris Beauvais", "BVA"), ("Larnaca, Cyprus", "LCA"), ("Istanbul", "IST/SAW"),
    ("Zurich", "ZRH"), ("Budapest Airport", "BUD"),
])
def test_airport_resolution(text, code):
    assert airports.resolve(text).code == code


@pytest.mark.parametrize("text", ["Greece", "Crete", "Athens or Thessaloniki", "XYZ", ""])
def test_airport_resolution_refuses_ambiguous_or_unknown(text):
    assert airports.resolve(text) is None


def test_places_normalized():
    d, flags = norm(origin="TLV", destination="London Luton")
    assert (d.origin, d.origin_airport, d.destination, d.destination_airport) == ("Tel Aviv", "TLV", "London", "LTN")
    d, _ = norm(destination="London")
    assert d.destination_airport == "LON"  # metro stays a metro
    d, _ = norm(destination="Istanbul")
    assert (d.destination, d.destination_airport) == ("Istanbul", "")  # IST/SAW isn't an IATA code
    d, _ = norm(destination="Greece")
    assert (d.destination, d.destination_airport) == ("Greece", "")
    d, flags = norm(destination="", destination_airport="prg")
    assert (d.destination, d.destination_airport) == ("Prague", "PRG") and not flags


def test_airport_code_flags():
    d, flags = norm(destination_airport="London Luton")
    assert d.destination_airport == "LTN" and "destination_airport_invalid" not in flags
    d, flags = norm(destination_airport="??")
    assert d.destination_airport == "PRG" and "destination_airport_invalid" in flags
    d, flags = norm(destination_airport="QQQ")
    assert d.destination_airport == "QQQ" and "destination_airport_unknown" in flags
    d, flags = norm(destination="Prague", destination_airport="BUD")
    assert d.destination_airport == "BUD" and "destination_mismatch" in flags


# --- currency --------------------------------------------------------------------

def test_currency_inferred_from_url():
    d, flags = norm(currency="RS", booking_url="https://www.elal.com/en/booking?curr=ILS")
    assert d.currency == "ILS" and "currency_inferred" in flags and "currency was 'RS'" in d.notes


def test_currency_inferred_from_amount_in_notes():
    d, flags = norm(currency="LAK", price=450, notes="Fare €420 + bag €30 = €450 total (~₪1,750)")
    assert d.currency == "EUR" and flags == ["currency_inferred"]


def test_currency_inferred_from_tld():
    d, _ = norm(currency="IDR", source_url="https://www.kayak.co.il/flights/TLV-PRG")
    assert d.currency == "ILS"


def test_currency_flags_without_evidence():
    d, flags = norm(currency="RS")
    assert d.currency == "RS" and "currency_invalid" in flags
    d, flags = norm(currency="HKD", price=2400)
    assert d.currency == "HKD" and "currency_unusual" in flags


def test_plausible_currencies_untouched():
    for code, price in (("EUR", 300), ("ILS", 1100), ("USD", 330), ("CZK", 7000), ("GBP", 250)):
        d, flags = norm(currency=code, price=price)
        assert d.currency == code and not flags, (code, flags)
    d, flags = normalize_deal(deal(currency="CHF"), SearchParams(currency="CHF"), TODAY)
    assert not flags  # the run's own preferred currency is always plausible


# --- prices ----------------------------------------------------------------------

def test_price_sanity_bands():
    assert "price_too_low" in norm(price=4, currency="USD")[1]
    assert "price_too_high" in norm(price=9000, currency="USD")[1]
    two = SearchParams(passengers=Passengers(adults=2))
    assert not normalize_deal(deal(price=600, currency="USD"), two, TODAY)[1]
    business = SearchParams(cabin_class="business")
    assert not normalize_deal(deal(price=4000, currency="USD"), business, TODAY)[1]
    # Long haul is allowed a lot more than a hop to Cyprus.
    assert "price_too_high" in norm(destination="Larnaca", price=2200, currency="USD")[1]
    assert not norm(destination="New York", price=2200, currency="USD")[1]


def test_fatal_deals_dropped_and_logged(caplog):
    caplog.set_level("INFO")
    kept = normalize_deals([deal(), deal(price=0), deal(origin="", destination="")], None, TODAY)
    assert len(kept) == 1
    assert "dropping deal" in caplog.text and "no_price" in caplog.text and "no_route" in caplog.text


# --- dates & times ----------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2026-10-14", "2026-10-14"),
    ("2026-10-14T06:10:00", "2026-10-14"),
    ("14 Oct 2026", "2026-10-14"),
    ("Oct 14", "2026-10-14"),
    ("Feb 3", "2027-02-03"),
    ("14/10/2026", "2026-10-14"),
    ("2026/10/14", "2026-10-14"),
    ("10/11/2026", "10/11/2026"),  # ambiguous day/month
    ("mid October", "mid October"),
    ("2026-10-14 to 2026-10-18", "2026-10-14 to 2026-10-18"),
    ("Oct 14-18", "Oct 14-18"),
    ("", ""),
])
def test_normalize_date(raw, expected):
    assert normalize_date(raw, TODAY) == expected


@pytest.mark.parametrize("raw,expected", [
    ("6:10", "06:10"), ("6:10 pm", "18:10"), ("12:05 AM", "00:05"), ("18h30", "18:30"),
    ("25:00", "25:00"), ("", ""), ("morning", "morning"),
])
def test_normalize_time(raw, expected):
    assert normalize_time(raw) == expected


def test_date_flags():
    assert "depart_date_inexact" in norm(depart_date="mid October")[1]
    assert "depart_date_missing" in norm(depart_date="")[1]
    assert "return_before_depart" in norm(return_date="2026-10-10")[1]


# --- the whole deal ------------------------------------------------------------------

DIRTY = [
    dict(origin="TLV", destination="Prague", airline="EL AL", price=1450, currency="RS",
         depart_date="Oct 14", booking_url="https://www.elal.com/?curr=ILS"),
    dict(origin="Tel Aviv", destination="London Luton", airline="Wizz Air UK", price=450, currency="LAK",
         depart_date="14/10/2026", flight_numbers="w95302", notes="fare €450 incl. bag"),
    dict(origin="TLV", destination="Athens", airline="Arkia + Israir", price=3, currency="USD",
         depart_date="mid October"),
    dict(origin="Tel Aviv", destination="Larnaca", airline="multiple", price=120, currency="HKD",
         depart_date="2026-10-20", return_date="2026-10-18"),
    dict(origin="TLV", destination="Rome", airline="Israir Airlines", price=0, currency="₪", depart_date=""),
]


def test_normalize_is_idempotent():
    for raw in DIRTY:
        once, flags = normalize_deal(FlightDeal(**raw), None, TODAY)
        twice, flags2 = normalize_deal(once, None, TODAY)
        assert twice == once and flags2 == flags, raw
        assert set(flags) <= set(FLAGS)
        assert once.quality_flags == flags


def test_postprocess_hook_normalizes_before_filters():
    params = SearchParams(currency="ILS", max_price=2000, trip_type="one_way")
    fresh = [
        deal(airline="EL AL", currency="RS", price=1450, return_date="", notes="₪1,450 one way"),
        deal(airline="El Al", currency="ILS", price=1450, return_date="", quality_flags=["price_too_high"]),
        deal(airline="Wizz", price=0, return_date=""),
    ]
    out = postprocess(fresh, params, date(2026, 9, 30))
    # Both El Al spellings are one itinerary now; the LLM's own flags are discarded.
    assert len(out) == 1
    [d] = out
    assert (d.airline, d.airline_code, d.currency) == ("El Al", "LY", "ILS")
    assert d.destination_airport == "PRG" and d.origin_airport == "TLV"


def test_postprocess_hook_sees_corrected_currency_for_budget():
    # 450 "LAK" is ~nothing, so a USD 300 budget would let it through; as EUR it's over.
    over = deal(currency="LAK", price=450, return_date="", notes="€450 per person")
    assert postprocess([over], SearchParams(currency="USD", max_price=300, trip_type="one_way"),
                       date(2026, 9, 30)) == []


# --- storage --------------------------------------------------------------------

def test_quality_flags_persist(temp_db):
    [cleaned] = normalize_deals([FlightDeal(**DIRTY[0] | {"currency": "RS", "booking_url": ""})], None, TODAY)
    assert cleaned.quality_flags == ["currency_invalid"]
    storage.save_deals("TLV -> Prague (October)", [cleaned, deal()])
    rows = {d.airline: d for d in storage.list_deals(dedupe=False)}
    assert rows["El Al"].quality_flags == ["currency_invalid"]
    assert rows["Wizz Air"].quality_flags == []


def test_dedupe_merges_spellings_on_unrepaired_rows(temp_db):
    storage.save_deals("TLV -> Prague (a)", [deal(airline="EL AL", origin="TLV")])
    storage.save_deals("TLV -> Prague (b)", [deal(airline="El Al", origin="Tel Aviv")])
    storage.save_deals("TLV -> Prague (c)", [deal(airline="", airline_code="LY", origin="Tel Aviv (TLV)")])
    [merged] = storage.list_deals()
    assert merged.times_seen == 3


def test_origin_filter_matches_airport_code(temp_db):
    storage.save_deals("TLV -> Prague (a)", normalize_deals([deal(origin="TLV")], None, TODAY))
    [d] = storage.list_deals(origin="TLV")
    assert d.origin == "Tel Aviv"


OLD_DEALS = """CREATE TABLE deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, search_query TEXT NOT NULL, origin TEXT NOT NULL,
    destination TEXT NOT NULL, airline TEXT NOT NULL, price REAL NOT NULL, currency TEXT NOT NULL,
    depart_date TEXT NOT NULL, return_date TEXT NOT NULL DEFAULT '', duration TEXT NOT NULL DEFAULT '',
    stops INTEGER NOT NULL DEFAULT 0, booking_url TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', found_at TEXT NOT NULL)"""

# Shapes from the production audit: legacy pre-v1.0 rows with free-text everything.
LEGACY_ROWS = [
    ("TLV -> Prague (October)", "TLV", "Prague", "EL AL", 1450, "RS", "Oct 14", "", "https://www.elal.com/?curr=ILS", ""),
    ("TLV -> Prague (October)", "TLV", "Prague", "El Al", 1450, "ILS", "2026-10-14", "", "", ""),
    ("TLV -> London (October)", "Tel Aviv", "London Luton", "Wizz Air UK", 450, "LAK", "14/10/2026", "", "", "fare €450 incl bag"),
    ("TLV -> London (October)", "Tel Aviv", "London", "Wizz Air", 199, "EUR", "mid October", "", "", ""),
    ("TLV -> Athens (November)", "TLV", "Athens", "Israir Airlines", 820, "₪", "Nov 3", "Nov 7", "", ""),
    ("TLV -> Athens (November)", "TLV", "Athens", "multiple", 290, "HKD", "2026-11-03", "", "", ""),
    ("TLV -> Larnaca (December)", "TLV", "Larnaca", "Arkia + Israir", 0, "USD", "", "", "", ""),
    ("TLV -> Rome (May)", "TLV", "Rome", "ITA", 199, "€", "2026-05-02", "", "", ""),
]


def _seed_legacy(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(OLD_DEALS)
    conn.executemany(
        """INSERT INTO deals (search_query, origin, destination, airline, price, currency, depart_date,
                              return_date, booking_url, notes, found_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-09-20T10:00:00+00:00')""", LEGACY_ROWS)
    conn.commit()
    conn.close()


def _dump(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT * FROM deals ORDER BY id").fetchall()
    finally:
        conn.close()


def test_repair_dry_run_then_apply_then_idempotent(temp_db, capsys):
    repair = _script("repair_deals")
    _seed_legacy(temp_db)
    before = _dump(temp_db)

    assert repair.main(["--db", str(temp_db)]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and f"{len(LEGACY_ROWS)} rows scanned" in out
    assert "airline: 'EL AL' -> 'El Al'" in out and "currency: 'RS' -> 'ILS'" in out
    assert _dump(temp_db) == before  # not a byte changed, not even the schema

    assert repair.main(["--db", str(temp_db), "--apply"]) == 0
    assert "Applied:" in capsys.readouterr().out
    storage.reset_state()
    rows = {d.id: d for d in storage.list_deals(dedupe=False)}
    assert len(rows) == len(LEGACY_ROWS)  # nothing deleted
    assert {d.airline for d in rows.values()} == {"El Al", "Wizz Air", "Israir", "Multiple airlines",
                                                  "Arkia + Israir", "ITA Airways"}
    assert rows[1].currency == "ILS" and "currency_inferred" in rows[1].quality_flags
    assert rows[1].depart_date == "2026-10-14" and rows[1].origin_airport == "TLV"
    assert rows[3].currency == "EUR" and rows[3].destination_airport == "LTN" and rows[3].airline_code == "W9"
    assert rows[5].return_date == "2026-11-07" and rows[5].currency == "ILS"
    assert rows[6].quality_flags == ["currency_unusual"]
    assert "no_price" in rows[7].quality_flags  # flagged, never deleted
    assert [rows[i].price for i in sorted(rows)] == [r[4] for r in LEGACY_ROWS]
    # The two El Al spellings are one deal now.
    assert sum(d.airline == "El Al" for d in storage.list_deals()) == 1

    after = _dump(temp_db)
    assert repair.main(["--db", str(temp_db)]) == 0
    assert "0 would change" in capsys.readouterr().out
    assert repair.main(["--db", str(temp_db), "--apply"]) == 0
    assert _dump(temp_db) == after


def test_repair_ignores_turso_with_db_flag(temp_db, monkeypatch, capsys):
    repair = _script("repair_deals")
    _seed_legacy(temp_db)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://must-not-be-used.turso.io")
    assert repair.main(["--db", str(temp_db)]) == 0
    assert f"SQLite {temp_db}" in capsys.readouterr().out


def test_normalize_date_rejects_qualified_and_far_guessed_dates():
    from datetime import date

    from app.normalize import normalize_date

    found = date(2026, 8, 25)
    assert normalize_date("after Oct 11, 2026", found) == "after Oct 11, 2026"
    assert normalize_date("around Oct 11", found) == "around Oct 11"
    assert normalize_date("Jul 23", found) == "Jul 23"          # would be a 2027 guess
    assert normalize_date("Sep 3", found) == "2026-09-03"
    assert normalize_date("Aug 20", date(2026, 8, 24)) == "2026-08-20"  # a week of slack
    assert normalize_date("Fri, Oct 2", found) == "2026-10-02"  # weekday matches
    assert normalize_date("Sat, Oct 2", found) == "Sat, Oct 2"  # weekday contradicts
    assert normalize_date("14th October 2026", found) == "2026-10-14"
    assert normalize_date("Jul 16, 2027", found) == "2027-07-16"  # explicit year is fine
