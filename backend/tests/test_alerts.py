"""Smart alerts: price stats, each rule, deal score, anti-spam, digest vs
instant delivery, and the watch API's stats. Searches and notifications are
faked; nothing touches the network."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent, alerts, main, storage, watcher
from app.alerts import RunBest, WatchResult
from app.models import AlertRecord, AlertSettings, FlightDeal, Passengers, SearchParams, StoredDeal, WatchCreate

NOW = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)


def runs(*prices, days_apart=1, end=NOW):
    """RunBests oldest first, the last one `days_apart` before `end`."""
    n = len(prices)
    return [RunBest(end - timedelta(days=days_apart * (n - i)), p) for i, p in enumerate(prices)]


def row(price, currency="USD", at=NOW, params=None):
    return {"found_at": at.isoformat(), "price": price, "currency": currency,
            "search_params": params.model_dump_json() if params else None}


# --- stats -------------------------------------------------------------------------

def test_run_bests_groups_converts_and_filters_kind():
    kind = alerts.fare_kind(None)
    two = SearchParams(passengers=Passengers(adults=2))
    t1, t2 = NOW - timedelta(days=2), NOW - timedelta(days=1)
    rows = [
        row(300, at=t1), row(280, at=t1),
        row(900, "ILS", at=t2),            # 900 ILS = 300 USD (fallback 3.0)
        row(100, at=t2, params=two),       # another fare kind: ignored
        row(50, "XXX", at=t2),             # unconvertible: ignored
    ]
    assert alerts.run_bests(rows, "USD", kind) == [RunBest(t1, 280), RunBest(t2, 300)]
    assert alerts.run_bests(rows, "USD", alerts.fare_kind(two)) == [RunBest(t2, 100)]


def test_price_stats():
    old = [RunBest(NOW - timedelta(days=60), 150)]  # outside the 30-day window
    history = old + runs(300, 320, 280, 310, 290)
    s = alerts.price_stats(history, "USD", NOW)
    assert (s.runs, s.last_best, s.low_30d, s.typical, s.all_time_low) == (6, 290, 280, 300, 150)
    assert s.last_run_at == history[-1].found_at
    assert 0 <= s.score <= 100
    assert alerts.price_stats([], "USD", NOW) is None


def test_deal_score():
    assert alerts.deal_score(300, [300, 300]) is None           # too little history
    assert alerts.deal_score(300, [300, 300, 300]) == 50        # same as always
    best = alerts.deal_score(250, [300, 310, 320, 305])
    worst = alerts.deal_score(400, [300, 310, 320, 305])
    mid = alerts.deal_score(308, [300, 310, 320, 305])
    assert best >= 88 and worst <= 15 and worst < mid < best
    assert alerts.deal_score(150, [300, 310, 320]) == 100       # far under: full marks


# --- rules -------------------------------------------------------------------------

def rules(price, prior, max_price=100, **settings):
    s = AlertSettings(**{"threshold": False, "new_low": False, "under_typical": False, "big_drop": False, **settings})
    return [h.rule for h in alerts.evaluate_rules(price, prior, s, max_price, "USD", NOW)]


def test_threshold_rule():
    assert rules(100, [], threshold=True) == ["threshold"]
    assert rules(101, [], threshold=True) == []


def test_new_low_rule_needs_five_runs_in_window():
    assert rules(279, runs(300, 290, 280, 310, 305), new_low=True) == ["new_low"]
    assert rules(280, runs(300, 290, 280, 310, 305), new_low=True) == []        # equal isn't lower
    assert rules(200, runs(300, 290, 280, 310), new_low=True) == []             # only 4 runs
    # older runs don't count: 5 runs, but 2 of them before the 30-day window
    assert rules(200, runs(300, 290, 280, 310, 305, days_apart=10), new_low=True) == []
    # a 60-day window sees them
    hit = alerts.evaluate_rules(200, runs(300, 290, 280, 310, 305, days_apart=10),
                                AlertSettings(threshold=False, new_low_days=60), 1, "USD", NOW)
    assert hit[0].text == "Lowest in 60 days (was $280)"


def test_under_typical_rule():
    prior = runs(300, 310, 290)  # median 300
    assert rules(240, prior, under_typical=True) == ["under_typical"]           # exactly 20% under
    assert rules(241, prior, under_typical=True) == []
    assert rules(270, prior, under_typical=True, under_typical_pct=10) == ["under_typical"]
    assert rules(100, runs(300, 310), under_typical=True) == []                 # needs 3 runs
    [hit] = alerts.evaluate_rules(240, prior, AlertSettings(threshold=False, new_low=False, big_drop=False),
                                  1, "USD", NOW)
    assert hit.text == "20% under typical ($300)"


def test_big_drop_rule():
    assert rules(255, runs(400, 300), big_drop=True) == ["big_drop"]            # 15% under the LAST run
    assert rules(256, runs(400, 300), big_drop=True) == []
    assert rules(100, [], big_drop=True) == []
    assert rules(270, runs(300), big_drop=True, big_drop_pct=10) == ["big_drop"]


def test_defaults_and_all_rules_together():
    s = AlertSettings()
    assert (s.threshold, s.new_low, s.new_low_days, s.under_typical, s.under_typical_pct, s.big_drop, s.big_drop_pct) == \
        (True, True, 30, True, 20, True, 15)
    hits = alerts.evaluate_rules(200, runs(300, 310, 290, 305, 300), s, 250, "USD", NOW)
    assert [h.rule for h in hits] == ["threshold", "new_low", "under_typical", "big_drop"]


# --- anti-spam ---------------------------------------------------------------------

def record(price, days_ago, fp="abc"):
    return AlertRecord(watch_id=1, fingerprint=fp, price=price, currency="USD", rule="threshold",
                       sent_at=NOW - timedelta(days=days_ago))


def test_suppression():
    assert alerts.suppressed_by("abc", 290, [], NOW) is None
    assert alerts.suppressed_by("abc", 290, [record(300, 2)], NOW) is not None        # 3% cheaper: spam
    assert alerts.suppressed_by("abc", 285, [record(300, 2)], NOW) is None            # 5% cheaper: news
    assert alerts.suppressed_by("abc", 300, [record(300, 8)], NOW) is None            # a week later
    assert alerts.suppressed_by("xyz", 300, [record(300, 1)], NOW) is None            # another fare
    # compares against the latest alert of that fare
    assert alerts.suppressed_by("abc", 270, [record(300, 5), record(280, 1)], NOW) is not None


def deal(price=250, currency="USD", **kw):
    base = dict(id=1, search_query="q", found_at=NOW, origin="TLV", destination="London", airline="Wizz Air",
                price=price, currency=currency, depart_date="2026-10-14", return_date="2026-10-18",
                flight_numbers="W6 2327 / W6 2328")
    base.update(kw)
    return StoredDeal(**base)


def test_fingerprint_ignores_price_but_not_flights():
    assert alerts.fare_fingerprint(deal(250)) == alerts.fare_fingerprint(deal(199))
    assert alerts.fare_fingerprint(deal()) != alerts.fare_fingerprint(deal(depart_date="2026-10-15"))
    assert alerts.fare_fingerprint(deal()) != alerts.fare_fingerprint(
        deal(search_params=SearchParams(passengers=Passengers(adults=2))))


# --- messages ----------------------------------------------------------------------

def make_watch_obj(**kw):
    from app.models import Watch

    base = dict(id=1, origin="TLV", destination="London", dates_hint="October", max_price=300,
                currency="USD", created_at=NOW, max_stops=0)
    base.update(kw)
    return Watch(**base)


def test_digest_format():
    w1, w2, w3 = make_watch_obj(), make_watch_obj(id=2, destination="Prague"), make_watch_obj(id=3, destination="Rome")
    hit = WatchResult(
        watch=w1, best_price=250, best=deal(airline="A&B Air"), link=("Wizz Air", "https://wizz.test/?a=1&b=2"),
        stats=alerts.price_stats(runs(300, 310, 290, 250), "USD", NOW),
        hits=[alerts.RuleHit("threshold", "At or under your $300 target"), alerts.RuleHit("new_low", "Lowest in 30 days")],
    )
    calm = WatchResult(watch=w2, best_price=410, best=deal(410, destination="Prague"),
                       stats=alerts.price_stats(runs(400, 420, 410, 410), "USD", NOW))
    failed = WatchResult(watch=w3, error="boom")
    title, plain, rich = alerts.format_digest([calm, hit, failed], NOW)
    assert title == "Flight Scout · 1 deal alert"
    assert rich.splitlines()[:2] == ["✈️ <b>Flight Scout</b> · Thu 1 Oct", "3 watches checked · 1 deal alert"]
    # the hit comes first, with its reasons, stats and one booking link
    assert rich.index("#1") < rich.index("#2") < rich.index("#3")
    assert "🔥 <b>TLV → London · #1</b>" in rich
    assert "<b>$250</b> · A&amp;B Air · direct" in rich          # HTML-escaped
    assert "14 Oct → 18 Oct" in rich
    assert "🎯 At or under your $300 target" in rich and "📉 Lowest in 30 days" in rich
    assert '<a href="https://wizz.test/?a=1&amp;b=2">Book on Wizz Air ›</a>' in rich
    assert "TLV → Prague · #2: $410 · score" in rich
    assert "⚠️ TLV → Rome · #3: search failed" in rich
    # plain text for ntfy: no tags, raw URL
    assert "<" not in plain.replace("<b>", "") and "Book on Wizz Air: https://wizz.test/?a=1&b=2" in plain
    # short lines for phones (links aside)
    assert all(len(line) <= 60 for line in plain.splitlines() if "http" not in line)


def test_digest_without_hits_and_instant_format():
    w = make_watch_obj(currency="EUR")
    calm = WatchResult(watch=w, best_price=320, best=deal(1000, "ILS"))
    title, plain, _ = alerts.format_digest([calm], NOW)
    assert title == "Flight Scout · no new deals" and "1 watch checked · no new deals" in plain
    calm.hits = [alerts.RuleHit("big_drop", "Down 20% since last check ($400)")]
    t, p, rich = alerts.format_instant(calm)
    assert t == "TLV → London: €320 — Down 20% since last check"
    assert "₪1,000 (~€320)" in p and "⬇️ Down 20% since last check" in rich


# --- the watcher: digest vs instant, logging, suppression -----------------------------

@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_send(title, message, click_url="", html=None, quiet=False):
        calls.append({"title": title, "message": message, "url": click_url, "html": html, "quiet": quiet})
        return ["telegram"]

    monkeypatch.setattr(watcher, "send_notification", fake_send)
    return calls


@pytest.fixture
def fares(monkeypatch):
    """Fake agent: each watch's next price comes from the list (default 250 USD)."""
    prices: dict[str, list[float]] = {}

    def fake_find_deals(origin, destination, dates_hint, *args, params=None, on_event=None, **kw):
        price = (prices.get(destination) or [250]).pop(0)
        return [FlightDeal(origin=origin, destination=destination, airline="Wizz Air", price=price,
                           currency="USD", depart_date="2026-10-14", return_date="2026-10-18")]

    monkeypatch.setattr(agent, "find_deals", fake_find_deals)
    return prices


def add_watch(**kw):
    base = dict(origin="TLV", destination="London", dates_hint="October", max_price=200, currency="USD")
    base.update(kw)
    return storage.add_watch(WatchCreate(**base))


def seed_runs(w, prices, days_ago_start=10):
    """Past runs of watch `w`, one per day, oldest first."""
    for i, price in enumerate(prices):
        stored = storage.save_deals(watcher.watch_label(w), [FlightDeal(
            origin="TLV", destination=w.destination, airline="El Al", price=price, currency="USD",
            depart_date="2026-10-20", return_date="2026-10-25")], watcher.watch_params(w))
        at = (datetime.now(timezone.utc) - timedelta(days=days_ago_start - i)).isoformat()
        storage._with_conn(lambda c: (c.execute("UPDATE deals SET found_at = ? WHERE id = ?", (at, stored[0].id)), c.commit()))
    storage._invalidate_cache()


def test_sweep_sends_one_digest_and_logs_hits(temp_db, sent, fares):
    london = add_watch()
    prague = add_watch(destination="Prague", max_price=100)
    seed_runs(london, [320, 330, 310, 325, 315])
    seed_runs(prague, [300, 305, 310, 300, 302])
    fares["London"] = [250]   # new low, 20% under typical, big drop (not under the 200 threshold)
    fares["Prague"] = [301]   # nothing

    summary = watcher.run_watches()
    assert len(sent) == 1, "one combined message, not one per watch"
    msg = sent[0]
    assert msg["quiet"] is False and msg["title"] == "Flight Scout · 1 deal alert"
    assert "#1" in msg["html"] and "#2" in msg["html"] and "📉 Lowest in 30 days" in msg["html"]
    by_id = {r["watch_id"]: r for r in summary["results"]}
    assert by_id[london.id]["rules"] == ["new_low", "under_typical", "big_drop"]
    assert by_id[london.id]["alerted"] is True and by_id[prague.id]["alerted"] is False
    assert summary["alerts_sent"] == 1 and summary["digest_channels"] == ["telegram"]
    [logged] = storage.list_alerts()
    assert (logged.watch_id, logged.price, logged.rule) == (london.id, 250, "new_low,under_typical,big_drop")

    # same fare next day, barely cheaper: no repeat alert, digest is quiet
    fares["London"], fares["Prague"] = [245], [301]
    summary = watcher.run_watches()
    assert len(sent) == 2 and sent[1]["quiet"] is True
    assert "already alerted" in sent[1]["html"]
    assert summary["results"][0]["suppressed"] is True and summary["alerts_sent"] == 0
    assert len(storage.list_alerts()) == 1

    # dropped a further 5%+: alert again
    fares["London"], fares["Prague"] = [230], [301]
    watcher.run_watches()
    assert sent[2]["quiet"] is False and len(storage.list_alerts()) == 2


def test_run_now_alerts_instantly_only_on_hit(temp_db, sent, fares):
    w = add_watch(max_price=260)
    fares["London"] = [300, 250]
    summary = watcher.run_watches(only_id=w.id)
    assert sent == [] and summary["results"][0]["alerted"] is False        # no rule fired: silent
    summary = watcher.run_watches(only_id=w.id)
    assert len(sent) == 1 and summary["results"][0]["alerted"] is True     # threshold + big drop
    assert sent[0]["title"] == "TLV → London: $250 — At or under your $260 target"
    assert "digest_channels" not in summary
    assert storage.list_alerts(w.id)[0].rule == "threshold,big_drop"


def test_notify_false_and_disabled_rules(temp_db, sent, fares):
    w = add_watch(max_price=900, alerts=AlertSettings(threshold=False))
    watcher.run_watches(only_id=w.id, notify=False)
    watcher.run_watches(notify=False)
    assert sent == [] and storage.list_alerts() == []
    summary = watcher.run_watches(only_id=w.id)
    assert summary["results"][0]["rules"] == [] and sent == []


def test_failed_watch_still_in_digest(temp_db, sent, monkeypatch):
    add_watch()

    def boom(*a, **kw):
        raise RuntimeError("search exploded")

    monkeypatch.setattr(agent, "find_deals", boom)
    summary = watcher.run_watches()
    assert summary["results"][0]["error"] == "search exploded"
    assert "search failed" in sent[0]["html"]


# --- API -----------------------------------------------------------------------------

@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return TestClient(main.app)


def test_watches_api_returns_stats_and_last_alert(client, sent, fares):
    w = add_watch()
    assert client.get("/api/watches").json()[0]["stats"] is None    # no runs yet
    seed_runs(w, [320, 330, 310, 325, 315])
    fares["London"] = [250]
    watcher.run_watches()
    [body] = client.get("/api/watches").json()
    s = body["stats"]
    assert (s["runs"], s["last_best"], s["low_30d"], s["all_time_low"], s["typical"]) == (6, 250, 250, 250, 317.5)
    assert s["score"] >= 88 and s["currency"] == "USD"
    assert body["last_alert"]["price"] == 250 and body["last_alert"]["rule"].startswith("new_low")
    assert client.get("/api/alerts").json()[0]["watch_id"] == w.id


def test_patch_alert_settings_partial_and_keeps_last_result(client):
    w = add_watch()
    storage.update_watch_run(w.id, 320)
    res = client.patch(f"/api/watches/{w.id}", json={"alerts": {"new_low": False, "big_drop_pct": 25}})
    assert res.status_code == 200, res.text
    a = res.json()["alerts"]
    assert (a["new_low"], a["big_drop_pct"], a["under_typical"], a["new_low_days"]) == (False, 25, True, 30)
    assert res.json()["last_best_price"] == 320               # alert tweaks don't reset results
    assert storage.get_watch(w.id).alerts.big_drop_pct == 25  # persisted
    assert client.patch(f"/api/watches/{w.id}", json={"alerts": {"big_drop_pct": 1}}).status_code == 422
    created = client.post("/api/watches", json={"origin": "TLV", "destination": "Rome", "max_price": 300,
                                                "alerts": {"threshold": False}}).json()
    assert created["alerts"]["threshold"] is False and created["alerts"]["new_low"] is True
