"""Telegram: webhook auth, command parsing, bot commands, webhook registration
and HTML delivery. The Bot API is always mocked — nothing leaves the process."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, notify, storage, telegram_bot, watcher
from app.models import FlightDeal, WatchCreate

SECRET = "s3cret-path_token-123"
CHAT = 4242


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", f"{CHAT}, 777")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)

    def refuse(*a, **kw):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)


@pytest.fixture
def api_calls(monkeypatch):
    calls = []

    def fake_api(method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": True}

    monkeypatch.setattr(notify, "telegram_api", fake_api)
    return calls


@pytest.fixture
def client(temp_db):
    return TestClient(main.app)


def update(text, chat=CHAT):
    return {"update_id": 1, "message": {"message_id": 5, "chat": {"id": chat, "type": "private"}, "text": text}}


def post(client, text, chat=CHAT, path=SECRET, header=SECRET):
    headers = {"X-Telegram-Bot-Api-Secret-Token": header} if header is not None else {}
    return client.post(f"/api/telegram/webhook/{path}", json=update(text, chat), headers=headers)


# --- webhook auth ------------------------------------------------------------------

def test_webhook_rejects_wrong_path_and_header(client):
    assert post(client, "/help", path="nope").status_code == 404
    assert post(client, "/help", path="ünïcode").status_code == 404
    assert post(client, "/help", header=None).status_code == 401
    assert post(client, "/help", header="wrong").status_code == 401
    ok = post(client, "/help")
    assert ok.status_code == 200 and ok.json()["method"] == "sendMessage"
    assert ok.json()["chat_id"] == CHAT and ok.json()["parse_mode"] == "HTML"


def test_webhook_disabled_without_secret(client, monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    assert post(client, "/help").status_code == 404
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "short")  # too weak to count as set
    assert client.post("/api/telegram/webhook/short", json=update("/help"),
                       headers={"X-Telegram-Bot-Api-Secret-Token": "short"}).status_code == 404


def test_unknown_chat_is_ignored(client):
    storage.add_watch(WatchCreate(origin="TLV", destination="London", max_price=300))
    res = post(client, "/pause 1", chat=999)
    assert res.status_code == 200 and res.json() == {}
    assert storage.get_watch(1).active is True                 # no action taken
    assert post(client, "/help", chat=777).json()["method"] == "sendMessage"  # second allowed chat


def test_non_commands_and_bad_updates_are_silent(client):
    assert post(client, "hello there").json() == {}
    assert client.post(f"/api/telegram/webhook/{SECRET}", json={"update_id": 2, "edited_message": {}},
                       headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}).json() == {}


# --- parsing -----------------------------------------------------------------------

def test_parse_command():
    assert telegram_bot.parse_command("/best 3") == ("best", "3")
    assert telegram_bot.parse_command("/Best@FlightScoutBot  #3 ") == ("best", "#3")
    assert telegram_bot.parse_command("/watches") == ("watches", "")
    assert telegram_bot.parse_command("best 3") is None


def test_parse_watch_example():
    w = telegram_bot.parse_watch("TLV PRG October under 300 direct 2 adults")
    assert (w.origin, w.destination, w.dates_hint, w.max_price, w.currency) == ("TLV", "PRG", "October", 300, "USD")
    assert (w.max_stops, w.direct_only, w.passengers.adults, w.trip_type) == (0, True, 2, "round_trip")


@pytest.mark.parametrize("args, expect", [
    ('"New York" lon first two weeks of November under €450 one-way business 2 adults 1 child checked bag',
     dict(origin="New York", destination="LON", dates_hint="first two weeks of November", max_price=450,
          currency="EUR", trip_type="one_way", cabin_class="business", checked_bag=True)),
    ("from TLV to ATH a long weekend in November up to 1,200 ils max 1 stop with cabin bag nearby",
     dict(destination="ATH", dates_hint="a long weekend in November", max_price=1200, currency="ILS",
          max_stops=1, cabin_bag=True, nearby_airports=True)),
    ("TLV BUD ≤250 eur", dict(max_price=250, currency="EUR", dates_hint="anytime in the next 3 months")),
    ("TLV BUD December $199 for 3 adults", dict(max_price=199, currency="USD", dates_hint="December")),
    ("TLV PRG first class in March under 4000", dict(cabin_class="first", dates_hint="in March")),
])
def test_parse_watch_variants(args, expect):
    w = telegram_bot.parse_watch(args)
    assert not isinstance(w, str), w
    for key, value in expect.items():
        assert getattr(w, key) == value, (key, getattr(w, key))


def test_parse_watch_party_and_bare_price_is_usd():
    w = telegram_bot.parse_watch("TLV PRG October under 300 2 adults 1 child 1 infant")
    assert (w.passengers.adults, w.passengers.children, w.passengers.infants, w.currency) == (2, 1, 1, "USD")


@pytest.mark.parametrize("price, expect", [
    ("under 250", (250, "USD")),
    ("under €250", (250, "EUR")),
    ("under ₪900", (900, "ILS")),
    ("under 300 ILS", (300, "ILS")),
    ("under 300 eur", (300, "EUR")),
    ("under 300eur", (300, "EUR")),
    ("under 250€", (250, "EUR")),
    ("under EUR 250", (250, "EUR")),
    ("under nis 900", (900, "ILS")),
    ("under $250", (250, "USD")),
    ("up to 1,200 shekels", (1200, "ILS")),
])
def test_parse_watch_currencies(price, expect):
    w = telegram_bot.parse_watch(f"TLV PRG November {price} direct")
    assert (w.max_price, w.currency, w.dates_hint, w.max_stops) == (*expect, "November", 0)


@pytest.mark.parametrize("args", ["", "TLV", "TLV PRG October", "TLV PRG October under abc", "TLV PRG under 300 12 adults"])
def test_parse_watch_errors(args):
    assert isinstance(telegram_bot.parse_watch(args), str)


# --- commands ----------------------------------------------------------------------

def seed_watch_with_run(price=250.0):
    w = storage.add_watch(WatchCreate(origin="TLV", destination="London", dates_hint="October",
                                      max_price=300, currency="USD", max_stops=0))
    storage.save_deals(watcher.watch_label(w), [FlightDeal(
        origin="TLV", destination="London", airline="Wizz Air", price=price, currency="USD",
        depart_date="2026-10-14", return_date="2026-10-18", origin_airport="TLV", destination_airport="LTN")],
        watcher.watch_params(w))
    storage.update_watch_run(w.id, price)
    return w


def test_watches_and_best(client):
    seed_watch_with_run()
    text = post(client, "/watches").json()["text"]
    assert "<b>#1</b> TLV → London · ≤ $300" in text and "last $250" in text
    best = post(client, "/best 1").json()["text"]
    assert "<b>$250</b> · Wizz Air · direct" in best and "14 Oct → 18 Oct" in best
    assert '<a href="https://' in best and "Book on" in best
    assert "No watch #9" in post(client, "/best 9").json()["text"]
    assert "Book on" in post(client, "/best").json()["text"]   # the only watch, no number needed


def test_pause_resume(client):
    seed_watch_with_run()
    assert "Paused" in post(client, "/pause 1").json()["text"]
    assert storage.get_watch(1).active is False
    assert storage.get_watch(1).last_best_price == 250             # pausing keeps results
    assert "paused" in post(client, "/run 1").json()["text"]
    assert "Resumed" in post(client, "/resume #1").json()["text"]
    assert storage.get_watch(1).active is True


def test_run_starts_job_and_replies_when_done(client, api_calls, monkeypatch):
    seed_watch_with_run()
    started = []
    monkeypatch.setattr(main, "_bot_run", lambda wid, chat: started.append((wid, chat)))
    reply = post(client, "/run 1").json()
    assert "Checking #1" in reply["text"] and started == [(1, str(CHAT))]


def test_bot_run_job_messages_chat(temp_db, api_calls, monkeypatch):
    w = seed_watch_with_run()
    summary = {"watches_run": 1, "alerts_sent": 0, "results": [{
        "watch_id": w.id, "route": "TLV → London", "best_price": 250, "currency": "USD",
        "rules": ["threshold"], "reasons": ["At or under your $300 target"], "suppressed": False}]}
    monkeypatch.setattr(watcher, "run_watches", lambda **kw: (kw["notify"] is False or 1 / 0) and summary)
    ran = []
    monkeypatch.setattr(main, "_start_job", lambda kind, q, work: ran.append(work("job1")))
    main._bot_run(w.id, str(CHAT))
    assert ran == [{"summary": summary}]
    [(method, payload)] = api_calls
    assert method == "sendMessage" and payload["chat_id"] == str(CHAT) and payload["parse_mode"] == "HTML"
    assert payload["text"].startswith("🔥 Deal!") and "🎯 At or under your $300 target" in payload["text"]


def test_watch_command_creates_watch(client):
    text = post(client, "/watch TLV PRG October under 300 direct 2 adults").json()["text"]
    [w] = storage.list_watches()
    assert (w.destination, w.max_price, w.max_stops, w.passengers.adults) == ("PRG", 300, 0, 2)
    assert "Watching <b>#1 TLV → PRG</b>" in text and "2 adults" in text and "direct" in text
    assert "Alert at ≤ $300 (say <code>under ₪900</code> for shekels)" in text
    text = post(client, "/watch TLV ROM May under ₪900").json()["text"]
    assert "≤ ₪900, plus" in text and storage.list_watches()[-1].currency == "ILS"
    # an existing ILS watch doesn't change what a bare number means
    text = post(client, "/watch TLV ATH December under 250").json()["text"]
    assert storage.list_watches()[-1].currency == "USD" and "≤ $250 (say" in text
    assert "I need a price" in post(client, "/watch TLV PRG October").json()["text"]
    assert len(storage.list_watches()) == 3


def test_help_and_unknown(client):
    assert "/watches" in post(client, "/start").json()["text"]
    assert "don't know" in post(client, "/fly").json()["text"]


def test_format_run_result_variants(temp_db):
    w = seed_watch_with_run()
    assert "failed" in telegram_bot.format_run_result(w.id, {"results": [{"error": "x"}]})
    assert "wasn't checked" in telegram_bot.format_run_result(w.id, {"results": []})
    calm = telegram_bot.format_run_result(w.id, {"results": [{"best_price": 250, "rules": [], "reasons": []}]})
    assert calm.startswith("✅ Checked") and "$250" in calm


# --- setup + delivery ---------------------------------------------------------------

def test_register_webhook(api_calls):
    out = telegram_bot.register_webhook("https://app.example.com/")
    (m1, p1), (m2, p2) = api_calls
    assert m1 == "setWebhook" and p1["url"] == f"https://app.example.com/api/telegram/webhook/{SECRET}"
    assert p1["secret_token"] == SECRET and p1["allowed_updates"] == ["message"]
    assert m2 == "setMyCommands" and {c["command"] for c in p2["commands"]} >= {"watches", "best", "run", "watch"}
    assert SECRET not in out["webhook"]
    with pytest.raises(RuntimeError):
        telegram_bot.register_webhook("http://localhost:8000")


def test_setup_endpoint(client, api_calls, monkeypatch):
    res = client.post("/api/telegram/setup", json={"base_url": "https://fs.example.com"})
    assert res.status_code == 200 and res.json()["ok"] is True
    assert api_calls[0][1]["url"].startswith("https://fs.example.com/api/telegram/webhook/")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    assert client.post("/api/telegram/setup", json={}).status_code == 401


def test_send_notification_html_and_ntfy(monkeypatch):
    posts = []

    class Ok:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(httpx, "post", lambda url, **kw: posts.append((url, kw)) or Ok())
    monkeypatch.setenv("NTFY_TOPIC", "my-topic")
    sent = notify.send_notification("Flight Scout · 1 deal alert", "plain body", "https://b.test/x",
                                    html="<b>rich</b>", quiet=True)
    assert sent == ["ntfy", "telegram"]
    ntfy_url, ntfy = posts[0]
    assert ntfy_url == "https://ntfy.sh/my-topic" and ntfy["content"] == b"plain body"
    assert ntfy["headers"]["Title"].startswith("=?UTF-8?B?") and ntfy["headers"]["Priority"] == "low"
    tg = [kw["json"] for url, kw in posts[1:]]
    assert {p["chat_id"] for p in tg} == {str(CHAT), "777"}
    assert all(p["text"] == "<b>rich</b>" and p["parse_mode"] == "HTML" and p["disable_notification"] for p in tg)
    assert all(url == "https://api.telegram.org/bot123:abc/sendMessage" for url, _ in posts[1:])
