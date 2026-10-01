"""Two-way Telegram bot over a webhook (no polling — free on Render).

Telegram POSTs every message to /api/telegram/webhook/<TELEGRAM_WEBHOOK_SECRET>
with the same secret in the X-Telegram-Bot-Api-Secret-Token header (see
main.py). Only chats listed in TELEGRAM_CHAT_ID get an answer. Immediate
replies ride back in the webhook response itself (Telegram executes a
`{"method": "sendMessage", ...}` body), so most commands make no outbound
call at all; /run replies later, from the job thread, via notify.send_telegram.

Register the webhook once per deploy URL:
    cd backend && uv run python -m app.telegram_bot https://your-app.onrender.com
or POST /api/telegram/setup from the deployed app (see README).

Commands: /watches, /best <#>, /run <#>, /pause <#>, /resume <#>,
/watch <from> <to> <dates...> under <price> [direct] [2 adults] ..., /help.
"""

from __future__ import annotations

import html
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from typing import Callable

from . import alerts, notify, storage, watcher
from .alerts import WatchResult, fmt_price
from .currency import _FALLBACK_PER_USD, normalize
from .models import Passengers, Watch, WatchCreate, WatchUpdate

# run_watch(watch_id, chat_id) starts a background run and messages the chat when done.
RunWatch = Callable[[int, str], None]

_SECRET_OK = re.compile(r"^[A-Za-z0-9_-]{16,256}$")

HELP = """✈️ <b>Flight Scout bot</b>
/watches — your watches, last best + score
/best 3 — cheapest fare of watch #3 + booking link
/run 3 — check watch #3 now (reply in ~2 min)
/pause 3 · /resume 3
/watch TLV PRG October under 300 direct 2 adults — new watch ($ unless you say €/₪/ILS…)
/help — this message"""

WATCH_USAGE = """Create a watch like:
<code>/watch TLV PRG October under 300 direct 2 adults</code>
Origin and destination first (quote multi-word names: "New York"), then any of:
dates text · <code>under 300</code> (USD) / <code>under €250</code> / <code>under 900 ILS</code> · <code>direct</code> · <code>max 1 stop</code> ·
<code>2 adults</code> <code>1 child</code> · <code>one-way</code> · <code>business</code> · <code>cabin bag</code> · <code>checked bag</code> · <code>nearby</code>"""


# --- auth -----------------------------------------------------------------------

def webhook_secret() -> str | None:
    """TELEGRAM_WEBHOOK_SECRET: both the URL path segment and the header value.
    Telegram allows 1-256 chars of A-Z a-z 0-9 _ - ; we insist on 16+."""
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    return secret if _SECRET_OK.match(secret) else None


def allowed_chat(chat_id) -> bool:
    return str(chat_id) in notify.chat_ids()


# --- parsing --------------------------------------------------------------------

def parse_command(text: str) -> tuple[str, str] | None:
    """'/best@FlightScoutBot 3' -> ('best', '3'); None for non-commands."""
    m = re.match(r"^/([A-Za-z_]+)(?:@\w+)?(?:\s+(.*))?$", (text or "").strip(), re.S)
    if not m:
        return None
    return m.group(1).lower(), (m.group(2) or "").strip()


_CURRENCY_WORDS = {
    "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "£": "GBP", "gbp": "GBP",
    "₪": "ILS", "ils": "ILS", "nis": "ILS", "shekel": "ILS", "shekels": "ILS",
}
_PRICE = re.compile(r"^([$€£₪])?(\d[\d,]*(?:\.\d+)?)([$€£₪]|[a-z]{3})?$", re.I)
_PRICE_WORDS = {"under", "below", "max", "<", "<=", "≤", "upto", "budget"}
_CONNECTORS = {"for", "with", "and", "+", ","}
_PAX = {"adult": "adults", "adults": "adults", "child": "children", "children": "children",
        "kid": "children", "kids": "children", "infant": "infants", "infants": "infants",
        "baby": "infants", "babies": "infants"}


def _currency_of(word: str | None) -> str | None:
    if not word:
        return None
    w = word.lower()
    if w in _CURRENCY_WORDS:
        return _CURRENCY_WORDS[w]
    code = normalize(word)
    return code if code in _FALLBACK_PER_USD else None


def parse_watch(args: str) -> WatchCreate | str:
    """'/watch' arguments -> a WatchCreate, or an error message (HTML).

    'TLV PRG October under 300 direct 2 adults' -> TLV -> PRG, dates 'October',
    max 300 USD, direct only, 2 adults. Every recognised criterion is consumed
    wherever it appears; what's left is the dates text. A bare price is USD (the
    app's default); a currency can be given as a symbol or code before or after
    the number: 'under €250', 'under 300 ILS', 'under eur 250'."""
    return _parse_watch(args)[0]


def _parse_watch(args: str) -> tuple[WatchCreate | str, bool]:
    """parse_watch, plus whether the currency was given explicitly."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    if tokens and tokens[0].lower() == "from":
        tokens = tokens[1:]
    if len(tokens) >= 3 and tokens[1].lower() == "to":
        tokens = [tokens[0]] + tokens[2:]
    if len(tokens) < 2:
        return WATCH_USAGE, False
    origin, destination, rest = tokens[0], tokens[1], tokens[2:]
    low = [t.lower().strip(",") for t in rest]
    used = [False] * len(rest)
    fields: dict = {}
    pax: dict = {}
    price: float | None = None
    currency: str | None = None

    def take(*idx: int) -> None:
        for i in idx:
            used[i] = True

    def nxt(i: int) -> str:
        return low[i] if i < len(low) else ""

    def price_at(j: int) -> tuple[float, str | None] | None:
        """The price in token j ("300", "$300", "300eur", "≤300"), with its currency if given."""
        pm = _PRICE.match(low[j].lstrip("≤<=")) if j < len(low) else None
        if not pm:
            return None
        word = pm.group(1) or pm.group(3)
        ccy = _currency_of(word)
        if word and not ccy:
            return None  # "300abc"
        return float(pm.group(2).replace(",", "")), ccy

    i = 0
    while i < len(low):
        t = low[i]
        m = _PRICE.match(t)
        # Price: "under 300 [eur]", "under eur 300", "max €300", "≤300", "up to 300", or a bare "$300".
        span = None
        if t in _PRICE_WORDS and not (t == "max" and nxt(i + 2).startswith("stop")) and price_at(i + 1):
            span = (i, i + 1)
        elif t in _PRICE_WORDS and (code := _currency_of(nxt(i + 1))) and (pc := price_at(i + 2)) and pc[1] is None:
            if price is None:
                price, currency = pc[0], code
                take(i, i + 1, i + 2)
            i += 3
            continue
        elif t == "up" and nxt(i + 1) == "to" and price_at(i + 2):
            span = (i, i + 2)
        elif (t[:1] in "≤<" or (m and m.group(1))) and price_at(i):
            span = (i, i)
        if span and price is None:
            price, currency = price_at(span[1])
            take(*range(span[0], span[1] + 1))
            i = span[1] + 1
            if currency is None and (c := _currency_of(nxt(i))):
                currency = c
                take(i)
                i += 1
            continue
        if t in ("direct", "nonstop", "non-stop"):
            fields["max_stops"] = 0
            take(i)
        elif m and not m.group(1) and not m.group(3) and nxt(i + 1).startswith("stop"):
            fields["max_stops"] = int(float(m.group(2)))
            take(i, i + 1)
            if i > 0 and low[i - 1] in ("max", "≤", "<=", "up"):
                take(i - 1)
            i += 2
            continue
        elif m and not m.group(1) and not m.group(3) and nxt(i + 1) in _PAX:
            pax[_PAX[nxt(i + 1)]] = int(float(m.group(2)))
            take(i, i + 1)
            i += 2
            continue
        elif t in ("one-way", "oneway"):
            fields["trip_type"] = "one_way"
            take(i)
        elif t == "one" and nxt(i + 1) == "way":
            fields["trip_type"] = "one_way"
            take(i, i + 1)
        elif t in ("round-trip", "roundtrip"):
            fields["trip_type"] = "round_trip"
            take(i)
        elif t == "round" and nxt(i + 1) == "trip":
            fields["trip_type"] = "round_trip"
            take(i, i + 1)
        elif t == "business":
            fields["cabin_class"] = "business"
            take(i, *([i + 1] if nxt(i + 1) == "class" else []))
        elif t == "premium":
            fields["cabin_class"] = "premium_economy"
            take(i, *([i + 1] if nxt(i + 1) == "economy" else []))
        elif t == "first" and nxt(i + 1) == "class":
            fields["cabin_class"] = "first"
            take(i, i + 1)
        elif t in ("cabin", "carry-on", "trolley", "hand") and (t == "trolley" or nxt(i + 1).startswith(("bag", "luggage"))):
            fields["cabin_bag"] = True
            take(i, *([i + 1] if t != "trolley" else []))
        elif t in ("checked", "20kg") or t in ("bag", "bags", "suitcase", "luggage"):
            fields["checked_bag"] = True
            take(i, *([i + 1] if t in ("checked", "20kg") and nxt(i + 1).startswith(("bag", "luggage")) else []))
        elif t in ("nearby", "nearby-airports"):
            fields["nearby_airports"] = True
            take(i, *([i + 1] if nxt(i + 1).startswith("airport") else []))
        i += 1

    if price is None or price <= 0:
        return "I need a price to alert at, e.g. <code>under 300</code>.\n\n" + WATCH_USAGE, False
    # Connectors next to consumed criteria ("for 2 adults", "with checked bag") aren't dates.
    for k in range(len(rest)):
        if not used[k] and low[k] in _CONNECTORS and (k + 1 >= len(rest) or used[k + 1]):
            used[k] = True
    dates = [t for t, u in zip(rest, used) if not u]
    while dates and dates[-1].lower() in _CONNECTORS:
        dates.pop()
    try:
        if pax:
            fields["passengers"] = Passengers(**{"adults": 1, **pax})
        return WatchCreate(
            origin=origin.upper() if len(origin) == 3 else origin,
            destination=destination.upper() if len(destination) == 3 else destination,
            dates_hint=" ".join(dates) or "anytime in the next 3 months",
            max_price=price,
            currency=currency or "USD",
            **fields,
        ), currency is not None
    except Exception as exc:  # noqa: BLE001 - pydantic range errors (e.g. 12 adults)
        errors = getattr(exc, "errors", None)
        detail = errors()[0]["msg"] if callable(errors) and errors() else str(exc).splitlines()[0]
        return f"That watch isn't valid: {html.escape(detail)}", False


# --- replies --------------------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _ago(when: datetime | None, now: datetime) -> str:
    if when is None:
        return "not run yet"
    secs = (now - (when if when.tzinfo else when.replace(tzinfo=timezone.utc))).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _enriched(now: datetime) -> list[Watch]:
    return alerts.enrich_watches(storage.list_watches(), storage.watch_sightings(), storage.list_alerts(), now)


def format_watches(watches: list[Watch], now: datetime) -> str:
    if not watches:
        return "No watches yet. Create one:\n<code>/watch TLV PRG October under 300</code>"
    lines = ["<b>Your watches</b>"]
    for w in watches:
        head = f"<b>#{w.id}</b> {_esc(w.origin)} → {_esc(w.destination)} · ≤ {fmt_price(w.max_price, w.currency)}"
        if not w.active:
            head += " · ⏸ paused"
        s = w.stats
        bits = []
        if s and s.last_best is not None:
            bits.append(f"last {fmt_price(s.last_best, w.currency)}")
        elif w.last_best_price is not None:
            bits.append(f"last {fmt_price(w.last_best_price, w.currency)}")
        if s and s.score is not None:
            bits.append(f"score {s.score}")
        bits.append(_ago(w.last_run_at, now))
        lines += [head, "   " + " · ".join(bits)]
    lines.append("\n/best 1 · /run 1 · /pause 1")
    return "\n".join(lines)


def format_best(w: Watch, now: datetime) -> str:
    deals = storage.watch_run_deals(w.id)
    price, best = watcher.cheapest(w, deals)
    head = f"<b>#{w.id} {_esc(w.origin)} → {_esc(w.destination)}</b> · {_esc(w.dates_hint)}"
    if best is None:
        return f"{head}\nNo fares stored yet — /run {w.id} to check now."
    r = WatchResult(watch=w, best_price=price, best=best, link=watcher.best_link_labeled(best), stats=w.stats)
    lines = [head, *alerts.fare_lines(r, rich=True)]
    stats = alerts.stats_line(r)
    if stats:
        lines.append(_esc(stats))
    lines.append(f"Found {_ago(best.found_at, now)} · target ≤ {fmt_price(w.max_price, w.currency)}")
    if r.link:
        label, url = r.link
        lines.append(f'<a href="{html.escape(url, quote=True)}">Book on {_esc(label)} ›</a>')
    return "\n".join(lines)


def format_run_result(watch_id: int, summary: dict, now: datetime | None = None) -> str:
    """The /run follow-up message, from the run's summary + the stored fares."""
    now = now or datetime.now(timezone.utc)
    rows = summary.get("results") or []
    row = rows[0] if rows else {}
    if row.get("error"):
        return f"⚠️ Check of watch #{watch_id} failed: {_esc(str(row['error'])[:200])}"
    w = next((x for x in _enriched(now) if x.id == watch_id), None)
    if w is None or not rows:
        return f"Watch #{watch_id} wasn't checked (deleted or paused?)."
    if row.get("best_price") is None:
        return f"✅ Checked #{w.id} {_esc(w.origin)} → {_esc(w.destination)}: no comparable fares this time."
    reasons = row.get("reasons") or []
    icons = [alerts.RULE_ICONS.get(rule, "•") for rule in row.get("rules") or []]
    head = "🔥 Deal!" if reasons and not row.get("suppressed") else "✅ Checked"
    lines = [head, *(f"{icon} {_esc(text)}" for icon, text in zip(icons, reasons))]
    if row.get("suppressed"):
        lines.append("(same fare as an alert you already got this week)")
    return "\n".join(lines) + "\n\n" + format_best(w, now)


def _reply(chat_id, text: str) -> dict:
    """A webhook response body Telegram executes as sendMessage."""
    return {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": alerts._clip(text),
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }


def _pick(arg: str, watches: list[Watch]) -> Watch | str:
    """'3' / '#3' -> watch 3; no argument picks the only watch there is."""
    m = re.match(r"^#?(\d+)$", arg.strip())
    if not arg.strip() and len(watches) == 1:
        return watches[0]
    if not m:
        return "Which watch? Send its number, e.g. <code>3</code> — /watches lists them."
    w = next((x for x in watches if x.id == int(m.group(1))), None)
    return w if w else f"No watch #{m.group(1)}. /watches lists them."


def handle_update(update: dict, run_watch: RunWatch, now: datetime | None = None) -> dict | None:
    """Answer one Telegram update. Returns the webhook reply body, or None to
    stay silent (non-message updates, unknown chats, non-command chatter)."""
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None or not allowed_chat(chat_id):
        return None
    parsed = parse_command(msg.get("text") or "")
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    cmd, arg = parsed

    if cmd in ("help", "start"):
        return _reply(chat_id, HELP)
    if cmd in ("watches", "list"):
        return _reply(chat_id, format_watches(_enriched(now), now))
    if cmd == "watch":
        parsed_watch, explicit = _parse_watch(arg)
        if isinstance(parsed_watch, str):
            return _reply(chat_id, parsed_watch)
        w = storage.add_watch(parsed_watch)
        words = ", ".join(_esc(c) for c in _criteria(w))
        hint = "" if explicit else " (say <code>under ₪900</code> for shekels)"
        return _reply(chat_id, (
            f"👀 Watching <b>#{w.id} {_esc(w.origin)} → {_esc(w.destination)}</b>\n"
            f"{_esc(w.dates_hint)}{' · ' + words if words else ''}\n"
            f"Alert at ≤ {fmt_price(w.max_price, w.currency)}{hint}, plus new lows and big drops.\n"
            f"/run {w.id} to check now."
        ))
    if cmd in ("best", "pause", "resume", "run"):
        watches = _enriched(now)
        picked = _pick(arg, watches)
        if isinstance(picked, str):
            return _reply(chat_id, picked)
        w = picked
        name = f"#{w.id} {_esc(w.origin)} → {_esc(w.destination)}"
        if cmd == "best":
            return _reply(chat_id, format_best(w, now))
        if cmd in ("pause", "resume"):
            fields, active = WatchUpdate(active=cmd == "resume").apply_to(w)
            storage.update_watch(w.id, fields, active)
            return _reply(chat_id, f"⏸ Paused {name}. /resume {w.id} to restart." if cmd == "pause"
                          else f"▶️ Resumed {name} — checked daily again.")
        if not w.active:
            return _reply(chat_id, f"{name} is paused. /resume {w.id} first.")
        run_watch(w.id, str(chat_id))
        return _reply(chat_id, f"🔎 Checking {name}… I'll message you when it's done (1–2 min).")
    return _reply(chat_id, "I don't know that one.\n\n" + HELP)


def _criteria(w: Watch) -> list[str]:
    from .models import _criteria_flags

    flags = [f for f in _criteria_flags(watcher.watch_params(w))]
    if w.max_stops == 0:
        flags.append("direct")
    if w.cabin_bag:
        flags.append("cabin bag")
    if w.checked_bag:
        flags.append("checked bag")
    return flags


# --- webhook registration ---------------------------------------------------------

COMMANDS = [
    {"command": "watches", "description": "Your watches with last best + score"},
    {"command": "best", "description": "Cheapest fare of a watch: /best 3"},
    {"command": "run", "description": "Check a watch now: /run 3"},
    {"command": "pause", "description": "Pause a watch: /pause 3"},
    {"command": "resume", "description": "Resume a watch: /resume 3"},
    {"command": "watch", "description": "New watch: /watch TLV PRG October under 300"},
    {"command": "help", "description": "How to use the bot"},
]


def webhook_url(base_url: str, secret: str) -> str:
    return f"{base_url.rstrip('/')}/api/telegram/webhook/{secret}"


def register_webhook(base_url: str) -> dict:
    """Point the bot at this deployment (setWebhook) and publish the command menu."""
    secret = webhook_secret()
    if not secret:
        raise RuntimeError("Set TELEGRAM_WEBHOOK_SECRET (16+ chars of A-Z a-z 0-9 _ -)")
    if not base_url.startswith("https://"):
        raise RuntimeError("Telegram webhooks need a public https:// URL")
    result = notify.telegram_api("setWebhook", {
        "url": webhook_url(base_url, secret),
        "secret_token": secret,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    })
    notify.telegram_api("setMyCommands", {"commands": COMMANDS})
    return {"ok": bool(result.get("ok")), "description": result.get("description", ""),
            "webhook": webhook_url(base_url, "<secret>")}


if __name__ == "__main__":  # uv run python -m app.telegram_bot https://your-app.onrender.com
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.telegram_bot <public https base URL>")
    print(register_webhook(sys.argv[1]))
