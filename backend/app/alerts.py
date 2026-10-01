"""Smart alerts: per-watch price stats, alert rules, anti-spam, deal score, and
the phone-friendly messages (daily digest + instant alert).

Everything here is pure — callers pass in the stored sightings, Google's price
insights, the sent-alert log and "now" — so it is tested without a database or
network. The watcher (app/watcher.py) wires it to storage and notify.

Vocabulary:
- a *sighting* is one stored deal row of a watch's run;
- a *run best* is the cheapest sighting of one run and trust tier (all rows of a
  run share found_at), converted into the watch currency;
- only runs of the watch's current *fare kind* count — party size, cabin and
  trip type — because a 2-adult total and a 1-adult fare aren't comparable.

Trust tiers (by the row's `source`, set by code — never by the LLM):
- **verified**: Google Flights live fares (source google_flights). The fare's
  price_confidence is NOT used: with a checked bag Google's fares are marked
  "estimated" (the bag fee isn't in them), yet every such run is priced the
  same way, so they stay comparable with each other;
- **explore**: Google Travel Explore prices (source google_explore) — Google's
  own cheapest-dates price, but not a bookable itinerary: no flight times, no
  booking token, no price history, and Explore's own cache may lag Google
  Flights. So they do NOT count as verified; they rank above the Aviasales
  cache because they come from Google for the exact party/stops/bags. In normal
  runs Explore only picks the dates and a verified Google Flights fare is
  stored for them; explore rows are the unconfirmed leftovers (budget out, or
  the pre-screen skipped the confirmation);
- **cached**: Aviasales cache fares (source aviasales) — real but possibly stale;
- **legacy**: everything else — pre-2.0 rows (no source) and LLM web results,
  whose prices were often teasers without bags. An LLM's own "verified" label
  is a claim, not a check, so it doesn't promote a row.

The stats and the smart rules rest on ONE basis (select_baseline), first match:
1. ``verified`` — at least OWN_RUNS_MIN verified runs: the watch's own history;
2. ``google``   — Google price insights for the watch's fare kind, at most
   GOOGLE_MAX_AGE_DAYS old: typical = middle of Google's typical range (else the
   median of its price history), lows from its price history, plus whatever
   verified runs the watch has;
3. ``verified`` — 1+ verified runs (thin: the run-count minimums still apply);
4. ``explore``  — Google Travel Explore runs (no verified data at all);
5. ``cached``   — Aviasales runs only;
6. ``legacy``   — unverified runs only: the pre-2.0 behaviour, labelled as such.
Legacy runs never mix into a trusted basis. While that basis has fewer than
LEGACY_SHOWN_BELOW runs (and no Google data) their numbers are reported apart
(WatchStats.legacy) for the UI to show muted; after that only their count.

Approximation: Google's price history is for the ONE date pair the live search
asked about (the run's cheapest pair), while a run best is the cheapest over all
the dates the run saw. So the history can sit a little above the watch's own
run bests; it is a stand-in for the route's price level until OWN_RUNS_MIN
verified runs exist.
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
    AlertRecord, AlertRule, AlertSettings, LegacyStats, PriceInsights, SearchParams, StoredDeal, Watch,
    WatchStats,
)

STATS_WINDOW_DAYS = 30
NEW_LOW_MIN_RUNS = 5        # "lowest in N days" needs this many earlier runs (or Google points) in the window
TYPICAL_MIN_RUNS = 3        # a median of fewer runs isn't "typical"
SCORE_MIN_RUNS = 3          # no deal score until there is some history
OWN_RUNS_MIN = 5            # verified runs before the watch's own history replaces Google's
GOOGLE_MAX_AGE_DAYS = 30    # older price insights are ignored
LEGACY_SHOWN_BELOW = 3      # legacy numbers are shown (muted) while the trusted basis has fewer runs
REALERT_DAYS = 7            # the same fare isn't re-alerted within this many days...
REALERT_DROP_PCT = 5        # ...unless it got at least this much cheaper
TELEGRAM_LIMIT = 4096

VERIFIED, EXPLORE, CACHED, LEGACY = "verified", "explore", "cached", "legacy"
_TIER_ORDER = {VERIFIED: 0, EXPLORE: 1, CACHED: 2, LEGACY: 3}


# --- price stats --------------------------------------------------------------

@dataclass(frozen=True)
class RunBest:
    found_at: datetime
    price: float  # in the watch currency
    tier: str = LEGACY


def source_tier(source: str | None) -> str:
    """Trust tier of a fare by its provenance (see the module docstring)."""
    if source == "google_flights":
        return VERIFIED
    if source == "google_explore":
        return EXPLORE
    if source == "aviasales":
        return CACHED
    return LEGACY


def _as_datetime(value) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fare_kind(params: SearchParams | None) -> tuple:
    """What makes two prices comparable: party, cabin, trip type. Legacy rows
    (no stored params) were 1 adult economy round trips."""
    p = params or SearchParams()
    pax = p.passengers
    return (pax.adults, pax.children, pax.infants, p.cabin_class, p.trip_type)


def insight_kind(params: SearchParams) -> str:
    """Key of the Google price insights that fit a watch: its fare kind plus the
    filters that change Google's price (stops, carry-on fees)."""
    return repr(fare_kind(params) + (params.max_stops, params.cabin_bag))


def _row_kind(raw) -> tuple:
    if isinstance(raw, SearchParams) or raw is None:
        return fare_kind(raw)
    try:
        return fare_kind(SearchParams.model_validate_json(raw) if raw else None)
    except Exception:
        return fare_kind(None)


def run_bests(rows: list[dict], currency: str, kind: tuple) -> list[RunBest]:
    """One RunBest per run and trust tier (rows grouped by found_at), oldest
    first. Rows are sightings with found_at/price/currency/search_params and
    optionally source; other fare kinds and unconvertible currencies are skipped."""
    best: dict[tuple[datetime, str], float] = {}
    for row in rows:
        if _row_kind(row.get("search_params")) != kind:
            continue
        price = convert(row["price"], row["currency"], currency)
        if price is None:
            continue
        key = (_as_datetime(row["found_at"]), source_tier(row.get("source")))
        best[key] = min(price, best.get(key, price))
    ordered = sorted(best.items(), key=lambda kv: (kv[0][0], _TIER_ORDER[kv[0][1]]))
    return [RunBest(at, round(p, 2), tier) for (at, tier), p in ordered]


def deal_rows(deals: list[StoredDeal], found_at: datetime) -> list[dict]:
    """A run's stored deals as sighting rows (for run_bests)."""
    return [{"found_at": found_at, "price": d.price, "currency": d.currency,
             "search_params": d.search_params, "source": d.source} for d in deals]


def _window(runs: list[RunBest], now: datetime, days: int) -> list[float]:
    cutoff = now - timedelta(days=days)
    return [r.price for r in runs if r.found_at >= cutoff]


@dataclass(frozen=True)
class GoogleHistory:
    """Google Flights' price insights of the watch's latest live search, in the
    watch currency: its typical range and the price history of that date pair."""

    typical_low: float | None
    typical_high: float | None
    points: tuple[tuple[datetime, float], ...]  # oldest first
    seen_at: datetime
    depart_date: str = ""
    return_date: str = ""

    @property
    def has_range(self) -> bool:
        return self.typical_low is not None and self.typical_high is not None

    def window(self, now: datetime, days: int) -> list[float]:
        cutoff = now - timedelta(days=days)
        return [p for at, p in self.points if cutoff <= at <= now]

    def typical(self, now: datetime) -> float | None:
        """Middle of Google's typical range, else the median of its last 30 days."""
        if self.has_range:
            return round((self.typical_low + self.typical_high) / 2, 2)
        recent = self.window(now, STATS_WINDOW_DAYS)
        return round(statistics.median(recent), 2) if len(recent) >= TYPICAL_MIN_RUNS else None


def google_history(insights: PriceInsights | dict | None, currency: str, now: datetime,
                   seen_at: datetime | None = None) -> GoogleHistory | None:
    """Price insights (a model, or a storage row carrying seen_at) converted
    into `currency`; None when stale, unconvertible, or saying nothing."""
    if insights is None:
        return None
    if isinstance(insights, dict):
        seen_at = seen_at or insights.get("seen_at")
        insights = PriceInsights.model_validate({k: v for k, v in insights.items() if k in PriceInsights.model_fields})
    seen = _as_datetime(seen_at) if seen_at else now
    if seen < now - timedelta(days=GOOGLE_MAX_AGE_DAYS):
        return None

    def conv(value):
        return None if value is None else convert(value, insights.currency, currency)

    lo, hi = conv(insights.typical_low), conv(insights.typical_high)
    if lo is None or hi is None or hi < lo:
        lo = hi = None
    points = []
    for ts, price in insights.price_history:
        value = conv(price)
        if value is not None:
            points.append((datetime.fromtimestamp(ts, timezone.utc), round(value, 2)))
    if lo is None and not points:
        return None
    return GoogleHistory(lo and round(lo, 2), hi and round(hi, 2), tuple(sorted(points)), seen,
                         insights.depart_date, insights.return_date)


def latest_google(rows: list[dict], params: SearchParams, currency: str,
                  now: datetime) -> GoogleHistory | None:
    """The newest stored insights of a watch (storage.watch_price_insights rows)
    that fit its fare kind."""
    kind = insight_kind(params)
    fitting = sorted((r for r in rows if r.get("kind") == kind), key=lambda r: str(r.get("seen_at")))
    return google_history(fitting[-1], currency, now) if fitting else None


@dataclass
class Baseline:
    """What a watch's stats and smart rules compare against (select_baseline)."""

    basis: str
    runs: list[RunBest]                    # the trusted runs, oldest first (basis google: the verified ones)
    google: GoogleHistory | None = None
    legacy: list[RunBest] = field(default_factory=list)  # left-out unverified runs

    def window(self, now: datetime, days: int) -> list[float]:
        """Prices of the last `days`: the basis' runs plus Google's history points."""
        out = _window(self.runs, now, days)
        return out + self.google.window(now, days) if self.google else out

    def typical(self, now: datetime, min_runs: int = TYPICAL_MIN_RUNS) -> float | None:
        if self.google:
            typical = self.google.typical(now)
            if typical is not None:
                return typical
        recent = _window(self.runs, now, STATS_WINDOW_DAYS)
        return round(statistics.median(recent), 2) if recent and len(recent) >= min_runs else None

    def typical_text(self, currency: str, now: datetime) -> str:
        """'$300', or "$285–390 per Google" on Google's basis."""
        if self.google and self.google.has_range:
            return f"{fmt_range(self.google.typical_low, self.google.typical_high, currency)} per Google"
        typical = self.typical(now, 1)
        text = fmt_price(typical, currency) if typical is not None else ""
        return f"{text} per Google" if text and self.google else text


def select_baseline(runs: list[RunBest], google: GoogleHistory | None = None) -> Baseline:
    """The single trust basis for a watch's numbers — see the module docstring."""
    tiers = {t: [r for r in runs if r.tier == t] for t in (VERIFIED, EXPLORE, CACHED, LEGACY)}
    verified, explore, cached, legacy = tiers[VERIFIED], tiers[EXPLORE], tiers[CACHED], tiers[LEGACY]
    if len(verified) >= OWN_RUNS_MIN:
        return Baseline(VERIFIED, verified, legacy=legacy)
    if google is not None:
        return Baseline("google", verified, google, legacy=legacy)
    if verified:
        return Baseline(VERIFIED, verified, legacy=legacy)
    if explore:
        return Baseline(EXPLORE, explore, legacy=legacy)
    if cached:
        return Baseline(CACHED, cached, legacy=legacy)
    return Baseline(LEGACY, legacy)


def deal_score(price: float, prior: list[float], typical: float | None = None) -> int | None:
    """0-100: how good `price` is against the route's earlier prices.

    75% percentile (share of earlier prices that were higher; ties count half),
    25% distance from `typical` (default: their median; 40% under = full marks,
    at it = half, 40% over = zero). A price equal to every earlier one scores
    50; a new low scores 88+. None until there are SCORE_MIN_RUNS earlier prices."""
    if len(prior) < SCORE_MIN_RUNS:
        return None
    above = sum(1 for p in prior if p > price) + 0.5 * sum(1 for p in prior if p == price)
    percentile = above / len(prior)
    return round(100 * (0.75 * percentile + 0.25 * _distance(price, typical or statistics.median(prior))))


def _distance(price: float, typical: float) -> float:
    discount = (typical - price) / typical if typical > 0 else 0.0
    return min(1.0, max(0.0, 0.5 + discount / 0.8))


def _score(last: RunBest | None, b: Baseline, now: datetime) -> int | None:
    if last is None:
        return None
    earlier = [r.price for r in b.runs if r.found_at < last.found_at]
    if not b.google:
        return deal_score(last.price, earlier)
    typical = b.google.typical(now)
    prices = b.google.window(now, STATS_WINDOW_DAYS) + _window([r for r in b.runs if r.found_at < last.found_at],
                                                               now, STATS_WINDOW_DAYS)
    score = deal_score(last.price, prices, typical)
    if score is None and typical is not None:
        # Typical range but too few points: the distance from it alone.
        score = round(100 * _distance(last.price, typical))
    return score


def _distinct_runs(runs: list[RunBest]) -> int:
    return len({r.found_at for r in runs})


def price_stats(runs: list[RunBest], currency: str, now: datetime,
                google: GoogleHistory | None = None) -> WatchStats | None:
    """Stats over a watch's run-bests (oldest first, every tier) on the basis
    select_baseline picks; None when it has no runs."""
    if not runs:
        return None
    b = select_baseline(runs, google)
    series = b.runs
    last = series[-1] if series else None
    recent = b.window(now, STATS_WINDOW_DAYS)
    everything = [r.price for r in series] + ([p for _, p in b.google.points] if b.google else [])
    stats = WatchStats(
        currency=currency,
        runs=_distinct_runs(runs),
        last_best=last.price if last else None,
        last_run_at=last.found_at if last else None,
        low_30d=min(recent) if recent else None,
        typical=b.typical(now, 1),
        all_time_low=min(everything) if everything else None,
        score=_score(last, b, now),
        basis=b.basis,
        basis_runs=len(series),
        history_points=len(b.google.window(now, STATS_WINDOW_DAYS)) if b.google else 0,
        typical_low=b.google.typical_low if b.google else None,
        typical_high=b.google.typical_high if b.google else None,
        legacy_runs=_distinct_runs(b.legacy) if b.basis != LEGACY else 0,
    )
    if b.legacy and b.basis not in (LEGACY, "google") and len(series) < LEGACY_SHOWN_BELOW:
        old = _window(b.legacy, now, STATS_WINDOW_DAYS)
        stats.legacy = LegacyStats(
            runs=_distinct_runs(b.legacy),
            low_30d=min(old) if old else None,
            typical=round(statistics.median(old), 2) if old else None,
            all_time_low=min(r.price for r in b.legacy),
        )
    return stats


# --- rules ----------------------------------------------------------------------

@dataclass(frozen=True)
class RuleHit:
    rule: AlertRule
    text: str  # short, phone-friendly reason, e.g. "Lowest in 30 days"


def evaluate_rules(price: float, prior: list[RunBest], settings: AlertSettings,
                   max_price: float, currency: str, now: datetime,
                   google: GoogleHistory | None = None) -> list[RuleHit]:
    """Which rules this run's best `price` triggers, given the watch's earlier
    runs `prior` (oldest first, this run excluded, every tier) and Google's
    price insights — compared on the basis select_baseline picks."""
    b = select_baseline(prior, google)
    per_google = " per Google" if b.google else ""
    hits: list[RuleHit] = []
    if settings.threshold and price <= max_price:
        hits.append(RuleHit("threshold", f"At or under your {fmt_price(max_price, currency)} target"))

    if settings.new_low:
        window = b.window(now, settings.new_low_days)
        if len(window) >= NEW_LOW_MIN_RUNS and price < min(window):
            hits.append(RuleHit("new_low", f"Lowest in {settings.new_low_days} days"
                                           f" (was {fmt_price(min(window), currency)}{per_google})"))

    if settings.under_typical:
        typical = b.typical(now)
        if typical and price <= typical * (1 - settings.under_typical_pct / 100):
            pct = round(100 * (typical - price) / typical)
            hits.append(RuleHit("under_typical", f"{pct}% under typical ({b.typical_text(currency, now)})"))

    if settings.big_drop and b.runs:
        last = b.runs[-1].price
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

def watch_stats(watch: Watch, rows: list[dict], now: datetime | None = None,
                insights: list[dict] | None = None) -> WatchStats | None:
    """`rows`: the watch's sightings; `insights`: its stored price insights."""
    now = now or datetime.now(timezone.utc)
    runs = run_bests(rows, watch.currency, fare_kind(watch))
    google = latest_google(insights or [], watch, watch.currency, now)
    return price_stats(runs, watch.currency, now, google)


def enrich_watches(watches: list[Watch], sightings: dict[int, list[dict]],
                   alerts: list[AlertRecord], now: datetime | None = None,
                   insights: dict[int, list[dict]] | None = None) -> list[Watch]:
    """Copies of `watches` carrying stats and their latest sent alert.
    `insights` (storage.watch_price_insights) seeds thin histories."""
    latest: dict[int, AlertRecord] = {}
    for a in alerts:
        if a.watch_id not in latest or _as_datetime(a.sent_at) > _as_datetime(latest[a.watch_id].sent_at):
            latest[a.watch_id] = a
    insights = insights or {}
    return [
        w.model_copy(update={
            "stats": watch_stats(w, sightings.get(w.id, []), now, insights.get(w.id)),
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


def fmt_range(low: float, high: float, currency: str) -> str:
    """$285–390 · 950–1,300 CZK."""
    code = normalize(currency)
    if code in _SYMBOLS:
        return f"{fmt_price(low, code)}–{high:,.0f}"
    return f"{low:,.0f}–{fmt_price(high, code)}"


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
    """'Score 72/100 · typical $300 · 30d low $280'; on Google's basis
    'typical $285–390 per Google'; on unverified runs only, '· estimates'."""
    s, ccy = r.stats, r.watch.currency
    bits = []
    if not s:
        return ""
    if s.score is not None:
        bits.append(f"Score {s.score}/100")
    depth = s.basis_runs + s.history_points
    if s.typical_low is not None and s.typical_high is not None:
        bits.append(f"typical {fmt_range(s.typical_low, s.typical_high, ccy)} per Google")
    elif s.typical is not None and depth > 1:
        bits.append(f"typical {fmt_price(s.typical, ccy)}" + (" per Google" if s.basis == "google" else ""))
    if s.low_30d is not None and depth > 1:
        bits.append(f"30d low {fmt_price(s.low_30d, ccy)}")
    if bits and s.basis == LEGACY:
        bits.append("estimates")
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


def format_digest(results: list[WatchResult], now: datetime, warning: str = "") -> tuple[str, str, str]:
    """The daily sweep's single message: (title, plain text, Telegram HTML).
    Alerting watches come first, then the rest in watch order; `warning` (the
    live-search budget projection, engine.watch_projection) closes it."""
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
        if warning:
            parts.append("⚠️ " + (html.escape(warning) if rich else warning))
        return _clip("\n\n".join(parts))

    return title, build(False), build(True)


def format_instant(r: WatchResult) -> tuple[str, str, str]:
    """A "Run now" hit: (title, plain text, Telegram HTML) for one watch."""
    w = r.watch
    title = f"{_route(w)}: {fmt_price(r.best_price, w.currency)} — {r.hits[0].text.split(' (')[0]}"
    return title, "\n".join(_block(r, False)), _clip("\n".join(_block(r, True)))
