"""Trustworthy watch stats (app/alerts.py): trust tiers, the basis rule,
seeding from Google's price insights, the hand-over to the watch's own verified
runs, rule texts, and the price_insights table. No network: SerpApi responses
come from fixtures, searches are faked."""

import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import alerts, engine, main, storage, watcher
from app.alerts import RunBest, WatchResult
from app.engine import SearchOutcome
from app.models import AlertSettings, FlightDeal, PriceInsights, SearchMeta, SearchParams, Watch, WatchCreate
from app.providers import serpapi
from app.providers.base import FareQuery

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def at(days_ago: float) -> datetime:
    return NOW - days_ago * DAY


def ts(days_ago: float) -> int:
    return int(at(days_ago).timestamp())


def series(tier, *prices, start=40, step=1):
    """RunBests of one tier, oldest first, `start` days ago then every `step` days."""
    return [RunBest(at(start - i * step), p, tier) for i, p in enumerate(prices)]


def insights(lo=285, hi=390, history=(), currency="USD"):
    return PriceInsights(route="TLV>LON", currency=currency, typical_low=lo, typical_high=hi,
                         price_history=list(history), depart_date="2026-11-03", return_date="2026-11-08")


def google(lo=285, hi=390, history=(), currency="USD", seen=NOW, now=NOW):
    return alerts.google_history(insights(lo, hi, history, currency), "USD", now, seen)


# 30 daily Google points around $300-$400 (the last one today).
HISTORY = [(ts(29 - i), 300 + (i * 37) % 100) for i in range(30)]


def watch(**kw):
    base = dict(id=1, origin="TLV", destination="London", dates_hint="November", max_price=250,
                currency="USD", created_at=NOW, max_stops=0)  # no bags: prices are fares (see test_bags)
    base.update(kw)
    return Watch(**base)


# --- tiers ---------------------------------------------------------------------------

def test_source_tiers_and_run_bests_split_a_run_by_tier():
    assert [alerts.source_tier(s) for s in ("google_flights", "aviasales", "web", "", None)] == \
        ["verified", "cached", "legacy", "legacy", "legacy"]
    t = at(1)
    rows = [{"found_at": t.isoformat(), "price": p, "currency": "USD", "search_params": None, "source": s}
            for p, s in ((324, "google_flights"), (340, "google_flights"), (290, "aviasales"), (82, ""))]
    assert alerts.run_bests(rows, "USD", alerts.fare_kind(None)) == [
        RunBest(t, 324, "verified"), RunBest(t, 290, "cached"), RunBest(t, 82, "legacy")]


def test_legacy_only_keeps_the_old_numbers_labelled_legacy():
    s = alerts.price_stats(series("legacy", 300, 320, 280, 310, 290, start=5), "USD", NOW)
    assert (s.basis, s.basis_runs, s.low_30d, s.typical, s.legacy_runs, s.legacy) == ("legacy", 5, 280, 300, 0, None)
    assert s.score is not None


def test_cached_counts_only_without_verified_data():
    runs = series("legacy", 82, 90, start=20) + series("cached", 300, 310, start=5)
    s = alerts.price_stats(runs, "USD", NOW)
    assert (s.basis, s.basis_runs, s.low_30d, s.legacy_runs) == ("cached", 2, 300, 2)
    runs += [RunBest(at(1), 330, "verified")]
    s = alerts.price_stats(runs, "USD", NOW)
    assert (s.basis, s.basis_runs, s.last_best, s.low_30d, s.typical) == ("verified", 1, 330, 330, 330)


def test_legacy_shown_apart_until_three_verified_runs():
    legacy = series("legacy", 82, 256, 69.27, 120, start=25)
    s = alerts.price_stats(legacy + series("verified", 324, 330, start=3), "USD", NOW)
    assert s.basis == "verified" and s.low_30d == 324 and s.all_time_low == 324
    assert s.legacy is not None and (s.legacy.runs, s.legacy.low_30d, s.legacy.all_time_low) == (4, 69.27, 69.27)
    assert s.score is None                                     # 1 earlier verified run: no score yet
    s = alerts.price_stats(legacy + series("verified", 324, 330, 318, start=3), "USD", NOW)
    assert s.legacy is None and s.legacy_runs == 4             # excluded, only counted
    assert (s.low_30d, s.typical, s.all_time_low) == (318, 324, 318)


# --- seeding from Google's price insights ---------------------------------------------

def test_serpapi_keeps_and_maps_price_history():
    data = json.loads((FIXTURES / "serpapi_round_trip.json").read_text())
    trimmed = serpapi._trim(data)
    assert trimmed["price_insights"]["price_history"] == data["price_insights"]["price_history"]
    ins = serpapi.map_insights(trimmed, FareQuery("TLV", "PRG", "2025-10-14", "2025-10-18", SearchParams()))
    assert (ins.typical_low, ins.typical_high) == (540, 760)
    assert ins.price_history == [(1756684800, 598.0), (1756771200, 610.0), (1759276800, 486.0)]
    assert serpapi._history([[1759276800000, 486], ["x", 1], [1, -5], "junk"]) == [(1759276800, 486.0)]
    one_way = json.loads((FIXTURES / "serpapi_one_way.json").read_text())
    assert serpapi.map_insights(one_way, FareQuery("TLV", "PRG", "2025-10-14", "", SearchParams())).price_history == []


def test_fixture_insights_seed_a_watch_with_one_verified_run():
    data = json.loads((FIXTURES / "serpapi_round_trip.json").read_text())
    ins = serpapi.map_insights(serpapi._trim(data), FareQuery("TLV", "PRG", "2025-10-14", "2025-10-18", SearchParams()))
    now = datetime(2025, 10, 2, tzinfo=timezone.utc)
    g = alerts.google_history(ins, "USD", now, now)
    s = alerts.price_stats([RunBest(now - DAY, 520, "verified")], "USD", now, g)
    assert (s.basis, s.typical, s.typical_low, s.typical_high) == ("google", 650, 540, 760)
    assert (s.low_30d, s.all_time_low, s.history_points) == (486, 486, 2)   # 1 Sep is outside 30 days
    assert s.score is not None and 40 <= s.score <= 80        # under typical, above Google's latest


def test_london_with_one_verified_run_and_google():
    """The live watch: 38 LLM-era runs (teasers from $69) and one real $324 fare."""
    legacy = [RunBest(at(60 - i), p, "legacy") for i, p in enumerate([82, 256, 69.27, 120, 300, 210] * 6 + [150, 256])]
    runs = legacy + [RunBest(at(0.1), 324, "verified")]
    before = alerts.price_stats(runs, "USD", NOW)
    assert (before.basis, before.low_30d, before.typical, before.score) == ("verified", 324, 324, None)
    assert before.legacy.runs == 38                            # shown muted, not mixed in
    s = alerts.price_stats(runs, "USD", NOW, google(285, 390, HISTORY))
    assert (s.basis, s.basis_runs, s.runs, s.legacy_runs, s.legacy) == ("google", 1, 39, 38, None)
    assert (s.typical, s.typical_low, s.typical_high, s.history_points) == (337.5, 285, 390, 30)
    assert s.low_30d == 300 and s.all_time_low == 300 and s.last_best == 324
    assert 50 <= s.score <= 85                                 # a fair price, not "expensive" (was 20)
    r = WatchResult(watch=watch(), best_price=324, stats=s)
    assert alerts.stats_line(r) == f"Score {s.score}/100 · typical $285–390 per Google · 30d low $300"


def test_range_without_history_and_history_without_range():
    s = alerts.price_stats([RunBest(at(1), 285, "verified")], "USD", NOW, google(285, 390))
    assert (s.typical, s.low_30d, s.history_points) == (337.5, 285, 0)
    assert s.score == round(100 * (0.5 + (337.5 - 285) / 337.5 / 0.8))   # distance from the range only
    s = alerts.price_stats([RunBest(at(1), 324, "verified")], "USD", NOW, google(None, None, HISTORY))
    assert s.basis == "google" and s.typical == statistics.median(p for _, p in HISTORY) and s.typical_low is None
    assert alerts.google_history(insights(None, None, ()), "USD", NOW) is None   # says nothing


def test_stale_or_unconvertible_google_is_ignored():
    assert google(seen=NOW - timedelta(days=31)) is None
    assert google(currency="XXX") is None
    ils = google(855, 1170, [(ts(1), 900)], currency="ILS")            # fallback rate 3.0
    assert (ils.typical_low, ils.typical_high, ils.points[0][1]) == (285, 390, 300)


def test_hand_over_to_own_runs_at_five_verified():
    g = google(285, 390, HISTORY)
    four = series("verified", 330, 340, 320, 335, start=4)
    assert alerts.price_stats(four, "USD", NOW, g).basis == "google"
    five = four + [RunBest(at(0), 325, "verified")]
    s = alerts.price_stats(five, "USD", NOW, g)
    assert (s.basis, s.basis_runs, s.typical, s.low_30d, s.history_points, s.typical_low) == \
        ("verified", 5, 330, 320, 0, None)


def test_latest_google_matches_the_fare_kind():
    w = watch()
    row = {**insights().model_dump(), "kind": alerts.insight_kind(w), "seen_at": NOW.isoformat()}
    other = {**insights(500, 600).model_dump(), "kind": alerts.insight_kind(watch(max_stops=None)),
             "seen_at": NOW.isoformat()}
    g = alerts.latest_google([other, row], w, "USD", NOW)
    assert (g.typical_low, g.typical_high) == (285, 390)
    assert alerts.latest_google([other], w, "USD", NOW) is None


# --- rules -----------------------------------------------------------------------------

def rules(price, prior, g=None, **settings):
    s = AlertSettings(**{"threshold": False, "new_low": False, "under_typical": False, "big_drop": False, **settings})
    return alerts.evaluate_rules(price, prior, s, 100, "USD", NOW, g)


def test_legacy_runs_no_longer_drive_rules_once_verified_exists():
    legacy = series("legacy", 400, 410, 420, 405, 415, start=10)
    assert [h.rule for h in rules(324, legacy, under_typical=True, big_drop=True)] == ["under_typical", "big_drop"]
    prior = legacy + [RunBest(at(2), 330, "verified")]
    assert rules(324, prior, under_typical=True, big_drop=True, new_low=True) == []
    # ...and cheap teasers don't make a real fare look like a rise either: big drop vs the last verified run
    teasers = series("legacy", 82, 69, 90, start=10) + [RunBest(at(2), 400, "verified")]
    assert [h.text for h in rules(324, teasers, big_drop=True)] == ["Down 19% since last check ($400)"]


def test_rules_on_googles_basis():
    g = google(285, 390, HISTORY)
    prior = [RunBest(at(2), 330, "verified")]
    [hit] = rules(270, prior, g, under_typical=True)
    assert hit.text == "20% under typical ($285–390 per Google)"
    [hit] = rules(290, prior, g, new_low=True)                    # under every Google point of 30 days
    assert hit.text == "Lowest in 30 days (was $300 per Google)"
    assert rules(300, prior, g, new_low=True) == []
    assert rules(320, prior, g, under_typical=True, new_low=True) == []
    # big drop only ever compares with the watch's own previous run, not Google's points
    assert [h.rule for h in rules(280, prior, g, big_drop=True)] == ["big_drop"]
    assert rules(280, [], g, big_drop=True) == []


def test_instant_title_keeps_the_short_reason():
    w = watch()
    deal = FlightDeal(origin="TLV", destination="LHR", airline="El Al", price=270, currency="USD",
                      depart_date="2026-11-03", return_date="2026-11-08", source="google_flights")
    r = WatchResult(watch=w, best_price=270, best=deal, hits=rules(270, [], google(285, 390), under_typical=True))
    title, plain, _ = alerts.format_instant(r)
    assert title == "TLV → London: $270 — 20% under typical"
    assert "💸 20% under typical ($285–390 per Google)" in plain
    assert alerts.fmt_range(950, 1300, "CZK") == "950–1,300 CZK"


def test_prescreen_uses_google_basis():
    w = watch(max_price=100)
    rows = [{"found_at": at(2).isoformat(), "price": 300, "currency": "USD",
             "search_params": watcher.watch_params(w).model_dump_json(), "source": "google_flights"}]
    live, why = engine.prescreen_decision(w, 320, rows, at(2), 10, NOW, google(285, 390, HISTORY))
    assert live and "new_low" in why                               # 272 would be under Google's 30-day low
    assert engine.prescreen_decision(w, 320, rows, at(2), 10, NOW)[0] is False


# --- storage + the watcher ---------------------------------------------------------------

def test_price_insights_table_is_an_additive_migration(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE watches (id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL, "
                 "destination TEXT NOT NULL, dates_hint TEXT NOT NULL, max_price REAL NOT NULL, "
                 "currency TEXT NOT NULL DEFAULT 'USD', active INTEGER NOT NULL DEFAULT 1, "
                 "created_at TEXT NOT NULL, last_run_at TEXT, last_best_price REAL)")
    conn.execute("INSERT INTO watches (origin, destination, dates_hint, max_price, created_at) "
                 "VALUES ('TLV', 'London', 'November', 250, ?)", (NOW.isoformat(),))
    conn.commit()
    conn.close()
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setattr(storage, "DB_PATH", db)
    storage.reset_state()
    try:
        [w] = storage.list_watches()
        assert storage.watch_price_insights() == {}
        storage.save_price_insights(w.id, "k", insights(history=HISTORY[:2]), NOW)
        storage.save_price_insights(w.id, "k", insights(300, 400, HISTORY[:3]), NOW)   # replaces
        [row] = storage.watch_price_insights()[w.id]
        assert (row["typical_low"], row["kind"], len(row["price_history"])) == (300, "k", 3)
        assert storage.delete_watch(w.id) and storage.watch_price_insights() == {}
    finally:
        storage.reset_state()


@pytest.fixture
def live(monkeypatch):
    """Fake watch search: Google Flights fares (+ insights) at the queued prices."""
    queue: list[tuple[float, PriceInsights | None]] = []

    def fake(watch, params, prior_rows, on_event=None, now=None):
        price, ins = queue.pop(0)
        deal = FlightDeal(origin="TLV", destination="LHR", airline="El Al", price=price, currency="USD",
                          depart_date="2026-11-03", return_date="2026-11-08", source="google_flights",
                          price_confidence="estimated")
        return SearchOutcome([deal], SearchMeta(sources=["google_flights"], price_insights=ins))

    monkeypatch.setattr(engine, "find_for_watch", fake)
    monkeypatch.setattr(watcher, "send_notification", lambda *a, **kw: ["telegram"])
    return queue


def test_watch_run_stores_insights_and_stats_use_them(temp_db, live, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    history = [(int((datetime.now(timezone.utc) - timedelta(days=29 - i)).timestamp()), 300 + (i * 37) % 100)
               for i in range(30)]
    w = storage.add_watch(WatchCreate(origin="TLV", destination="London", dates_hint="November",
                                      max_price=250, max_stops=0, checked_bag=True))
    live.append((324, insights(285, 390, history)))
    [row] = watcher.run_watches()["results"]
    # Google's typical range is for the fare; the watch compares prices with its
    # checked bag, so the range moves up by this run's bag fees.
    bag_fees = row["best_price"] - 324
    assert bag_fees > 0
    assert row["stats"]["basis"] == "google" and row["stats"]["typical"] == round(337.5 + bag_fees, 2)
    [stored] = storage.watch_price_insights()[w.id]
    assert stored["kind"] == alerts.insight_kind(w) and len(stored["price_history"]) == 30

    # a cached-only run (no insights) still uses the stored ones
    live.append((330, None))
    [row] = watcher.run_watches()["results"]
    assert row["stats"]["basis"] == "google" and row["stats"]["basis_runs"] == 2

    [body] = TestClient(main.app).get("/api/watches").json()
    s = body["stats"]
    assert (s["basis"], s["basis_runs"]) == ("google", 2)
    assert s["typical_low"] > 285 and s["typical_high"] - s["typical_low"] == 105  # Google's range + bag fees
