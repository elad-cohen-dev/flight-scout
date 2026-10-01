"""Smart alerts: per-watch price stats, alert rules, anti-spam, deal score, and
the phone-friendly messages (daily digest + instant alert).

Everything here is pure — callers pass in the stored sightings, the sent-alert
log and "now" — so it is tested without a database or network. The watcher
(app/watcher.py) wires it to storage and notify.

Vocabulary:
- a *sighting* is one stored deal row of a watch's run;
- a *run best* is the cheapest sighting of one run (all rows of a run share
  found_at), converted into the watch currency;
- only runs of the watch's current *fare kind* count — party size, cabin and
  trip type — because a 2-adult total and a 1-adult fare aren't comparable.
"""

from __future__ import annotations

import hashlib
import html
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .currency import convert, normalize
from .models import (
    AlertRecord, AlertRule, AlertSettings, SearchParams, StoredDeal, Watch, WatchStats,
)

STATS_WINDOW_DAYS = 30
NEW_LOW_MIN_RUNS = 5        # "lowest in N days" needs this many earlier runs in the window
TYPICAL_MIN_RUNS = 3        # a median of fewer runs isn't "typical"
SCORE_MIN_RUNS = 3          # no deal score until there is some history
REALERT_DAYS = 7            # the same fare isn't re-alerted within this many days...
REALERT_DROP_PCT = 5        # ...unless it got at least this much cheaper
TELEGRAM_LIMIT = 4096


# --- price stats --------------------------------------------------------------

@dataclass(frozen=True)
class RunBest:
    found_at: datetime
    price: float  # in the watch currency


def _as_datetime(value) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fare_kind(params: SearchParams | None) -> tuple:
    """What makes two prices comparable: party, cabin, trip type. Legacy rows
    (no stored params) were 1 adult economy round trips."""
    p = params or SearchParams()
    pax = p.passengers
    return (pax.adults, pax.children, pax.infants, p.cabin_class, p.trip_type)


def _row_kind(raw) -> tuple:
    if isinstance(raw, SearchParams) or raw is None:
        return fare_kind(raw)
    try:
        return fare_kind(SearchParams.model_validate_json(raw) if raw else None)
    except Exception:
        return fare_kind(None)


def run_bests(rows: list[dict], currency: str, kind: tuple) -> list[RunBest]:
    """One RunBest per run (rows grouped by found_at), oldest first. Rows are
    sightings with found_at/price/currency/search_params; other fare kinds and
    unconvertible currencies are skipped."""
    best: dict[datetime, float] = {}
    for row in rows:
        if _row_kind(row.get("search_params")) != kind:
            continue
        price = convert(row["price"], row["currency"], currency)
        if price is None:
            continue
        at = _as_datetime(row["found_at"])
        best[at] = min(price, best.get(at, price))
    return [RunBest(at, round(p, 2)) for at, p in sorted(best.items())]


def _window(runs: list[RunBest], now: datetime, days: int) -> list[float]:
    cutoff = now - timedelta(days=days)
    return [r.price for r in runs if r.found_at >= cutoff]


def deal_score(price: float, prior: list[float]) -> int | None:
    """0-100: how good `price` is against the route's earlier run-bests.

    75% percentile (share of earlier runs that were pricier; ties count half),
    25% distance from their median (40% under = full marks, at the median = half,
    40% over = zero). A price equal to every earlier one scores 50; a new low
    scores 88+. None until there are SCORE_MIN_RUNS earlier runs."""
    if len(prior) < SCORE_MIN_RUNS:
        return None
    above = sum(1 for p in prior if p > price) + 0.5 * sum(1 for p in prior if p == price)
    percentile = above / len(prior)
    median = statistics.median(prior)
    discount = (median - price) / median if median > 0 else 0.0
    distance = min(1.0, max(0.0, 0.5 + discount / 0.8))
    return round(100 * (0.75 * percentile + 0.25 * distance))


def price_stats(runs: list[RunBest], currency: str, now: datetime) -> WatchStats | None:
    """Stats over a watch's run-bests (oldest first); None when it has none."""
    if not runs:
        return None
    recent = _window(runs, now, STATS_WINDOW_DAYS)
    last = runs[-1]
    return WatchStats(
        currency=currency,
        runs=len(runs),
        last_best=last.price,
        last_run_at=last.found_at,
        low_30d=min(recent) if recent else None,
        typical=round(statistics.median(recent), 2) if recent else None,
        all_time_low=min(r.price for r in runs),
        score=deal_score(last.price, [r.price for r in runs[:-1]]),
    )


# --- rules ----------------------------------------------------------------------

@dataclass(frozen=True)
class RuleHit:
    rule: AlertRule
    text: str  # short, phone-friendly reason, e.g. "Lowest in 30 days"


def evaluate_rules(price: float, prior: list[RunBest], settings: AlertSettings,
                   max_price: float, currency: str, now: datetime) -> list[RuleHit]:
    """Which rules this run's best `price` triggers, given the watch's earlier
    runs `prior` (oldest first, this run excluded)."""
    hits: list[RuleHit] = []
    if settings.threshold and price <= max_price:
        hits.append(RuleHit("threshold", f"At or under your {fmt_price(max_price, currency)} target"))

    if settings.new_low:
        window = _window(prior, now, settings.new_low_days)
        if len(window) >= NEW_LOW_MIN_RUNS and price < min(window):
            hits.append(RuleHit("new_low", f"Lowest in {settings.new_low_days} days"
                                           f" (was {fmt_price(min(window), currency)})"))

    if settings.under_typical:
        window = _window(prior, now, STATS_WINDOW_DAYS)
        if len(window) >= TYPICAL_MIN_RUNS:
            typical = statistics.median(window)
            if typical > 0 and price <= typical * (1 - settings.under_typical_pct / 100):
                pct = round(100 * (typical - price) / typical)
                hits.append(RuleHit("under_typical", f"{pct}% under typical ({fmt_price(typical, currency)})"))

    if settings.big_drop and prior:
        last = prior[-1].price
        if last > 0 and price <= last * (1 - settings.big_drop_pct / 100):
            pct = round(100 * (last - price) / last)
            hits.append(RuleHit("big_drop", f"Down {pct}% since last check ({fmt_price(last, currency)})"))
    return hits


# --- anti-spam ------------------------------------------------------------------

def fare_fingerprint(deal: StoredDeal) -> str:
    """Identity of a fare for re-alerting: same flights/dates/party, any price."""
    parts = [
        deal.airline, deal.airline_code, deal.origin_airport or deal.origin,
        deal.destination_airport or deal.destination, deal.depart_date, deal.return_date,
        deal.flight_numbers.replace(" ", ""), repr(fare_kind(deal.search_params)),
    ]
    raw = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def suppressed_by(fingerprint: str, price: float, recent: list[AlertRecord],
                  now: datetime) -> AlertRecord | None:
    """The earlier alert that makes this one spam, if any: the same fare alerted
    within REALERT_DAYS, and not REALERT_DROP_PCT cheaper since."""
    cutoff = now - timedelta(days=REALERT_DAYS)
    same = [a for a in recent if a.fingerprint == fingerprint and _as_datetime(a.sent_at) >= cutoff]
    if not same:
        return None
    latest = max(same, key=lambda a: _as_datetime(a.sent_at))
    return None if price <= latest.price * (1 - REALERT_DROP_PCT / 100) else latest


# --- API enrichment -------------------------------------------------------------

def watch_stats(watch: Watch, rows: list[dict], now: datetime | None = None) -> WatchStats | None:
    runs = run_bests(rows, watch.currency, fare_kind(watch))
    return price_stats(runs, watch.currency, now or datetime.now(timezone.utc))


def enrich_watches(watches: list[Watch], sightings: dict[int, list[dict]],
                   alerts: list[AlertRecord], now: datetime | None = None) -> list[Watch]:
    """Copies of `watches` carrying stats and their latest sent alert."""
    latest: dict[int, AlertRecord] = {}
    for a in alerts:
        if a.watch_id not in latest or _as_datetime(a.sent_at) > _as_datetime(latest[a.watch_id].sent_at):
            latest[a.watch_id] = a
    return [
        w.model_copy(update={
            "stats": watch_stats(w, sightings.get(w.id, []), now),
            "last_alert": latest.get(w.id),
        })
        for w in watches
    ]


# --- messages -------------------------------------------------------------------

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "ILS": "₪"}
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RULE_ICONS = {"threshold": "🎯", "new_low": "📉", "under_typical": "💸", "big_drop": "⬇️"}


def fmt_price(amount: float, currency: str) -> str:
    """₪1,009 · $289 · 1,350 CZK — whole units, as people read fares."""
    code = normalize(currency)
    symbol = _SYMBOLS.get(code)
    return f"{symbol}{amount:,.0f}" if symbol else f"{amount:,.0f} {code}"


def fmt_date(text: str) -> str:
    """'2026-10-14' -> '14 Oct'; anything else (e.g. 'mid October') unchanged."""
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", text or "")
    if not m:
        return (text or "").strip()
    return f"{int(m.group(3))} {_MONTHS[int(m.group(2)) - 1]}"


@dataclass
class WatchResult:
    """One watch's outcome in a run, as the messages need it."""

    watch: Watch
    best_price: float | None = None        # watch currency
    best: StoredDeal | None = None
    link: tuple[str, str] | None = None    # (label, url) of the best booking link
    stats: WatchStats | None = None        # including this run
    hits: list[RuleHit] = field(default_factory=list)
    suppressed: AlertRecord | None = None  # set when the hits are a repeat
    error: str | None = None

    @property
    def alerting(self) -> bool:
        return bool(self.hits) and self.suppressed is None


def _route(w: Watch) -> str:
    return f"{w.origin} → {w.destination}"


def fare_lines(r: WatchResult, rich: bool) -> list[str]:
    """Price line + dates line of the best deal; HTML-escaped when `rich`."""
    esc = html.escape if rich else (lambda s: s)
    d, w = r.best, r.watch
    price = fmt_price(r.best_price, w.currency)
    if d.currency != w.currency:
        price = f"{fmt_price(d.price, d.currency)} (~{price})"
    if w.passengers.total > 1 and d.price_per_person:
        price += f" · {fmt_price(d.price_per_person, d.currency)} pp"
    stops = "direct" if d.stops == 0 else f"{d.stops} stop{'s' if d.stops > 1 else ''}"
    first = f"{('<b>' + esc(price) + '</b>') if rich else price} · {esc(d.airline)} · {stops}"
    dates = fmt_date(d.depart_date) + (f" {d.depart_time}" if d.depart_time else "")
    if d.return_date:
        dates += f" → {fmt_date(d.return_date)}"
    return [first, esc(dates)]


def stats_line(r: WatchResult) -> str:
    s, ccy = r.stats, r.watch.currency
    bits = []
    if s and s.score is not None:
        bits.append(f"Score {s.score}/100")
    if s and s.typical is not None and s.runs > 1:
        bits.append(f"typical {fmt_price(s.typical, ccy)}")
    if s and s.low_30d is not None and s.runs > 1:
        bits.append(f"30d low {fmt_price(s.low_30d, ccy)}")
    return " · ".join(bits)


def _block(r: WatchResult, rich: bool) -> list[str]:
    esc = html.escape if rich else (lambda s: s)
    w = r.watch
    head = f"{_route(w)} · #{w.id}"
    if r.error:
        return [f"⚠️ {esc(head)}: search failed"]
    if r.best is None:
        return [f"{esc(head)}: no comparable fares"]
    if not r.alerting:
        line = f"{esc(head)}: {fmt_price(r.best_price, w.currency)}"
        score = r.stats.score if r.stats else None
        if score is not None:
            line += f" · score {score}"
        if r.suppressed is not None:
            line += " · already alerted"
        return [line]
    lines = [f"🔥 <b>{esc(head)}</b>" if rich else f"🔥 {head}"]
    lines += fare_lines(r, rich)
    lines += [f"{RULE_ICONS[h.rule]} {esc(h.text)}" for h in r.hits]
    stats = stats_line(r)
    if stats:
        lines.append(esc(stats))
    if r.link:
        label, url = r.link
        lines.append(f'<a href="{html.escape(url, quote=True)}">Book on {esc(label)} ›</a>' if rich
                     else f"Book on {label}: {url}")
    return lines


def _clip(text: str) -> str:
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 2] + "\n…"


def _day(now: datetime) -> str:
    return f"{now.strftime('%a')} {now.day} {_MONTHS[now.month - 1]}"


def format_digest(results: list[WatchResult], now: datetime) -> tuple[str, str, str]:
    """The daily sweep's single message: (title, plain text, Telegram HTML).
    Alerting watches come first, then the rest in watch order."""
    hits = [r for r in results if r.alerting]
    rest = [r for r in results if not r.alerting]
    n_watch = f"{len(results)} watch{'es' if len(results) != 1 else ''}"
    n_hit = f"{len(hits)} deal alert{'s' if len(hits) != 1 else ''}" if hits else "no new deals"
    title = f"Flight Scout · {n_hit}"
    summary = f"{n_watch} checked · {n_hit}"

    def build(rich: bool) -> str:
        head = f"✈️ <b>Flight Scout</b> · {_day(now)}" if rich else f"✈️ Flight Scout · {_day(now)}"
        parts = [head + "\n" + summary]
        parts += ["\n".join(_block(r, rich)) for r in hits]
        if rest:
            parts.append("\n".join(line for r in rest for line in _block(r, rich)))
        return _clip("\n\n".join(parts))

    return title, build(False), build(True)


def format_instant(r: WatchResult) -> tuple[str, str, str]:
    """A "Run now" hit: (title, plain text, Telegram HTML) for one watch."""
    w = r.watch
    title = f"{_route(w)}: {fmt_price(r.best_price, w.currency)} — {r.hits[0].text.split(' (')[0]}"
    return title, "\n".join(_block(r, False)), _clip("\n".join(_block(r, True)))
