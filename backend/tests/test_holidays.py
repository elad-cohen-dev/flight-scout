"""Holiday windows (app/holidays.py): the shipped calendar's integrity, the
search window a holiday fills, the return capped at the vacation's last free
day, and the flexible-date path (Explore -> Google Flights) restricted to it.
Provider HTTP is faked (tests/test_explore.py fixtures)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import engine, holidays, main
from app.holidays import HolidayWindow
from app.models import FlightDeal, SearchParams, WatchCreate
from tests.test_explore import NOW, TODAY, http, web  # noqa: F401 — fixtures

REQUIRED = {"rosh_hashana", "yom_kippur", "sukkot", "hanukkah", "passover", "shavuot", "summer", "independence_day"}


# --- the shipped calendar -------------------------------------------------------

def test_calendar_covers_both_school_years():
    ws = holidays.windows()
    assert len({w.id for w in ws}) == len(ws)
    for year in ("2026-27", "2027-28"):
        assert {w.kind for w in ws if w.school_year == year} == REQUIRED
    for w in ws:
        assert w.start <= w.end and w.depart_from <= w.depart_to <= w.end
        assert 0 < w.nights_min <= w.nights_max <= (w.end - w.start).days
        assert w.holiday_start is None or w.start <= w.holiday_start <= w.end
        assert w.sources and all(s.startswith("https://") for s in w.sources)
    assert all(w.confirmed for w in ws if w.school_year == "2026-27")
    assert not any(w.confirmed for w in ws if w.school_year == "2027-28")  # predicted until the Ministry publishes


def test_known_dates_2026_27():
    """Ministry of Education calendar תשפ"ז (see data/holidays.json for sources)."""
    hanukkah = holidays.get("hanukkah-2026")
    assert (hanukkah.start, hanukkah.end) == (date(2026, 12, 5), date(2026, 12, 12))
    assert hanukkah.name == "Hanukkah 2026"
    passover = holidays.get("PASSOVER-2027")  # ids are case-insensitive
    assert (passover.start, passover.end) == (date(2027, 4, 13), date(2027, 4, 28))
    assert passover.holiday_start == date(2027, 4, 22)
    assert holidays.get("independence-day-2027").holiday_start == date(2027, 5, 12)
    assert holidays.get("independence-day-2028").holiday_start == date(2028, 5, 2)  # postponed from Monday
    summer = holidays.get("summer-2027")
    assert (summer.start, summer.end) == (date(2027, 7, 1), date(2027, 8, 31))
    assert holidays.get("nope") is None and holidays.get("") is None and holidays.name_of("nope") == ""


def test_upcoming_drops_windows_too_late_to_fly():
    ids = [w.id for w in holidays.upcoming(date(2026, 10, 14))]
    assert ids[0] == "hanukkah-2026" and "sukkot-2026" not in ids
    assert ids == sorted(ids, key=lambda i: holidays.get(i).start)
    # A window stays until its last departure day (end - nights_min).
    late = holidays.get("hanukkah-2026")
    assert "hanukkah-2026" in [w.id for w in holidays.upcoming(late.depart_to)]
    assert "hanukkah-2026" not in [w.id for w in holidays.upcoming(late.end)]
    assert len(holidays.upcoming(date(2026, 10, 14), include_past=True)) == len(holidays.windows())


def test_api_lists_upcoming_holidays(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    client = TestClient(main.app)
    body = client.get("/api/holidays").json()
    assert body and all(w["depart_to"] >= date.today().isoformat() for w in body)
    first = body[0]
    assert {"id", "name", "start", "end", "depart_from", "depart_to", "nights_min", "nights_max",
            "confirmed", "sources"} <= set(first)
    assert len(client.get("/api/holidays?all=true").json()) == len(holidays.windows())


def test_unreadable_calendar_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(holidays, "DATA_PATH", tmp_path / "missing.json")
    holidays.windows.cache_clear()
    try:
        assert holidays.windows() == () and holidays.get("hanukkah-2026") is None
    finally:
        holidays.windows.cache_clear()


# --- the search window ------------------------------------------------------------

@pytest.fixture
def november_break(monkeypatch):
    """A made-up mid-November break, inside the London fixtures' Explore answer."""
    def window(start, end, lo=4, hi=7, wid="test-break-2026"):
        w = HolidayWindow(id=wid, name="Test break 2026", kind="hanukkah", school_year="2026-27",
                          start=start, end=end, nights_min=lo, nights_max=hi, depart_from=start,
                          depart_to=end.fromordinal(end.toordinal() - lo), sources=["https://example.org"])
        monkeypatch.setattr(holidays, "windows", lambda: (w,))
        return w
    return window


def test_holiday_plan_is_the_vacation_window(november_break):
    w = november_break(date(2026, 11, 15), date(2026, 11, 25))
    plan = engine.plan_dates("Test break 2026", SearchParams(holiday=w.id, nights_min=4, nights_max=7), TODAY)
    assert plan.pairs == [] and (plan.start, plan.end) == (date(2026, 11, 15), date(2026, 11, 21))
    assert plan.return_by == date(2026, 11, 25) and plan.fallback == ("2026-11-15", "2026-11-22")
    # The form's own window (filled from the holiday) wins inside the vacation; never outside it.
    narrowed = engine.plan_dates("x", SearchParams(holiday=w.id, depart_from=date(2026, 11, 17),
                                                   depart_to=date(2026, 12, 30)), TODAY)
    assert (narrowed.start, narrowed.end) == (date(2026, 11, 17), date(2026, 11, 25))
    one_way = engine.plan_dates("x", SearchParams(holiday=w.id, trip_type="one_way"), TODAY)
    assert one_way.return_by is None and one_way.fallback == ("2026-11-15", "")
    # Today is inside the window: departures from today.
    assert engine.plan_dates("x", SearchParams(holiday=w.id), date(2026, 11, 18)).start == date(2026, 11, 18)


def test_unknown_holiday_falls_back_to_the_text():
    plan = engine.plan_dates("November", SearchParams(holiday="no-such-break"), TODAY)
    assert (plan.start, plan.end) == (date(2026, 11, 1), date(2026, 11, 30)) and plan.return_by is None


def test_return_is_capped_at_the_last_free_day(november_break):
    w = november_break(date(2026, 11, 15), date(2026, 11, 22), lo=3, hi=7)
    params = SearchParams(holiday=w.id, nights_min=3, nights_max=7)
    plan = engine.plan_dates("x", params, TODAY)
    deal = FlightDeal(origin="TLV", destination="LTN", airline="W6", price=389, currency="USD",
                      depart_date="2026-11-17", return_date="2026-11-24")
    assert engine._fit_pair(deal, plan, params) == (("2026-11-17", "2026-11-22"), False)  # 5 nights, back by the 22nd
    late = deal.model_copy(update={"depart_date": "2026-11-19", "return_date": "2026-11-26"})
    assert engine._fit_pair(late, plan, params) == (("2026-11-19", "2026-11-22"), False)
    too_late = deal.model_copy(update={"depart_date": "2026-11-20", "return_date": "2026-11-27"})
    assert engine._fit_pair(too_late, plan, params) == (None, False)  # 2 nights < nights_min
    assert engine._fits_holiday(deal, plan) is False and engine._fits_holiday(late.model_copy(
        update={"return_date": "2026-11-22"}), plan) is True


def test_holiday_search_finds_the_cheapest_week_inside_it(http, web, november_break):
    w = november_break(date(2026, 11, 15), date(2026, 11, 25))
    params = SearchParams(holiday=w.id, depart_from=w.depart_from, depart_to=w.depart_to,
                          nights_min=w.nights_min, nights_max=w.nights_max, max_stops=0)
    out = engine.search("TLV", "London", w.name, params, today=TODAY)
    # One Explore call for November, then Explore's cheapest pair confirmed live.
    assert http.engines() == ["google_travel_explore", "google_flights"] and web == []
    flights = http[-1][1]
    assert (flights["outbound_date"], flights["return_date"]) == ("2026-11-17", "2026-11-24")
    assert out.meta.holiday == "Test break 2026" and out.meta.live_searches == 2
    assert out.deals and all(d.return_date <= "2026-11-25" for d in out.deals)
    assert out.meta.advice is not None  # buy-or-wait for the best deal


def test_holiday_beyond_explores_horizon_checks_its_first_week_live(http, web, november_break):
    w = november_break(date(2027, 5, 9), date(2027, 5, 15), lo=3, hi=5)
    params = SearchParams(holiday=w.id, nights_min=3, nights_max=5)
    out = engine.search("TLV", "London", w.name, params, today=TODAY)
    assert http.engines() == ["google_flights"]  # Explore only reaches six months: no Explore call
    flights = http[-1][1]
    assert (flights["outbound_date"], flights["return_date"]) == ("2027-05-09", "2027-05-14")
    assert out.meta.holiday == "Test break 2026"


def test_holiday_is_kept_on_watches(temp_db):
    from app import storage
    watch = storage.add_watch(WatchCreate(origin="TLV", destination="London", dates_hint="Hanukkah 2026",
                                          max_price=900, holiday="hanukkah-2026",
                                          depart_from=date(2026, 12, 5), depart_to=date(2026, 12, 8)))
    again = storage.get_watch(watch.id)
    assert again.holiday == "hanukkah-2026" and again.depart_to == date(2026, 12, 8)
