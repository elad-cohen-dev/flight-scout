"""Buy or wait: one verdict and a one-line reason for a fare.

    advise(Signals) -> Advice("buy_now" | "good_price" | "wait" | "not_enough_data")

Inputs, all in the watch / search currency and all prices WITH the requested
bags (app/bags.py; Google's insights arrive already shifted by the bag fees):

- Google's price insights: the typical range (best), else its price_level;
- the watch's own history: the typical price and 30-day low of its runs
  (app/alerts.py stats) and the last few run bests (the trend);
- days to departure, and whether the route is long-haul.

The rules, conservative on purpose — "Wait" is only said when time is on the
family's side, and every verdict says why:

1. Where the price sits: *low* (under Google's typical range, or 10%+ under the
   typical price), *high* (over the range, or 10%+ over), else *typical*. No
   reference at all -> "Not enough data" (inside the last-minute window, "Buy
   now": fares that close rarely fall, so waiting for data costs more).
2. The clock: short-haul fares (Europe from TLV) usually climb inside ~3–5
   weeks of departure (RISE_DAYS_SHORT = 35), long-haul ~2 months
   (RISE_DAYS_LONG = 60); inside two weeks (LAST_MINUTE_DAYS) they rarely drop.
3. Verdict:
   - low:     close to departure or a 30-day low -> Buy now; else Good price;
   - typical: last minute -> Buy now; inside the rise window or climbing over
              the last checks -> Good price; else Wait;
   - high:    last minute -> Buy now (waiting rarely helps); else Wait.

Pure — callers pass the numbers and "today" — so it is tested without a
database or network. `for_search` and `for_watch` build the Signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from . import airports
from .alerts import fmt_price, fmt_range
from .models import Advice, PriceInsights, WatchStats

BUY, GOOD, WAIT, UNKNOWN = "buy_now", "good_price", "wait", "not_enough_data"
LABELS = {BUY: "Buy now", GOOD: "Good price", WAIT: "Wait", UNKNOWN: "Not enough data"}

RISE_DAYS_SHORT = 35     # short-haul fares usually climb inside ~3-5 weeks
RISE_DAYS_LONG = 60      # ...long-haul inside ~2 months
LAST_MINUTE_DAYS = 14    # inside two weeks a fare rarely gets cheaper
LEVEL_PCT = 0.10         # 10% under / over the typical price = low / high
TREND_RUNS = 3           # run bests that make a trend
TREND_PCT = 0.05         # ...moving at least 5% end to end
TYPICAL_MIN_DEPTH = 3    # own runs + Google history points before "typical" counts
LONG_HAUL_KM = 4500      # TLV-London ~3,600 km, TLV-Bangkok ~7,000 km


@dataclass(frozen=True)
class Signals:
    price: float                       # the fare with bags
    currency: str
    days_out: int | None = None        # days to departure; None = unknown
    typical_low: float | None = None   # Google's typical range, with bags
    typical_high: float | None = None
    typical: float | None = None       # the watch's typical price (median of its runs)
    price_level: str = ""              # Google's own 'low' | 'typical' | 'high' (bare fare)
    low_30d: float | None = None       # lowest of the 30 days BEFORE this price
    recent: tuple[float, ...] = ()     # latest run bests, oldest first (this one last)
    long_haul: bool = False
    score: int | None = None           # the watch's 0-100 deal score (alerts.deal_score)


GREAT_SCORE = 80  # the score the Watches tab calls a "great deal"


def _pct(a: float, b: float) -> int:
    return round(100 * abs(a - b) / b) if b else 0


def level(s: Signals) -> tuple[str, str]:
    """('low' | 'typical' | 'high' | '', the phrase that says so)."""
    p, ccy = s.price, s.currency
    if s.typical_low is not None and s.typical_high is not None:
        span = fmt_range(s.typical_low, s.typical_high, ccy)
        if p < s.typical_low:
            return "low", f"{_pct(p, s.typical_low)}% under Google's typical {span}"
        if p > s.typical_high:
            return "high", f"{_pct(p, s.typical_high)}% over Google's typical {span}"
        return "typical", f"inside Google's typical {span}"
    if s.typical:
        typical = fmt_price(s.typical, ccy)
        if p <= s.typical * (1 - LEVEL_PCT):
            return "low", f"{_pct(p, s.typical)}% under the typical {typical}"
        if p >= s.typical * (1 + LEVEL_PCT):
            return "high", f"{_pct(p, s.typical)}% over the typical {typical}"
        return "typical", f"about the typical {typical}"
    if s.price_level in ("low", "typical", "high"):
        word = {"low": "low", "typical": "typical", "high": "high"}[s.price_level]
        return s.price_level, f"Google rates the fare {word}"
    return "", ""


def trend(recent: tuple[float, ...] | list[float]) -> str:
    """'rising' / 'falling' over the last TREND_RUNS run bests (each step the
    same way, TREND_PCT end to end), else 'flat' — '' with too few runs."""
    last = list(recent)[-TREND_RUNS:]
    if len(last) < TREND_RUNS or last[0] <= 0:
        return ""
    steps = [b - a for a, b in zip(last, last[1:])]
    move = (last[-1] - last[0]) / last[0]
    if all(d >= 0 for d in steps) and move >= TREND_PCT:
        return "rising"
    if all(d <= 0 for d in steps) and move <= -TREND_PCT:
        return "falling"
    return "flat"


def _days(n: int) -> str:
    return "tomorrow" if n == 1 else "today" if n == 0 else f"{n} days out"


def _make(verdict: str, reason: str, days_out: int | None) -> Advice:
    reason = reason[:1].upper() + reason[1:]
    return Advice(verdict=verdict, label=LABELS[verdict], reason=reason, days_out=days_out)


def advise(s: Signals) -> Advice:
    """The verdict for one price; see the module docstring for the rules."""
    days = s.days_out
    if days is not None and days < 0:
        return _make(UNKNOWN, "the departure date has passed", days)
    rise = RISE_DAYS_LONG if s.long_haul else RISE_DAYS_SHORT
    haul = "long-haul" if s.long_haul else "short-haul"
    last_minute = days is not None and days <= LAST_MINUTE_DAYS
    close = days is not None and days <= rise
    when = f", {_days(days)}" if days is not None else ""
    lvl, why = level(s)
    move = trend(s.recent)

    if not lvl:
        if last_minute:
            return _make(BUY, f"departure {_days(days)} and fares this close rarely drop "
                              "(no price history to compare)", days)
        return _make(UNKNOWN, "no typical price yet — a live check with Google's insights "
                              "or a few more checks will tell", days)

    if lvl == "low":
        if close:
            return _make(BUY, f"{why}{when} — {haul} fares usually climb from here", days)
        if s.low_30d is not None and s.price <= s.low_30d:
            return _make(BUY, f"{why} and the lowest in 30 days", days)
        return _make(GOOD, f"{why}{when}; it could still dip, but this is a good fare", days)

    if lvl == "typical":
        if last_minute:
            return _make(BUY, f"{why}{when} — unlikely to get cheaper this close", days)
        # A price inside the typical band can still be the best this watch has
        # seen; then "wait" would contradict the deal score next to it.
        if s.low_30d is not None and s.price <= s.low_30d:
            return _make(GOOD, f"{why}, but the lowest of your checks in 30 days{when}", days)
        if s.score is not None and s.score >= GREAT_SCORE:
            return _make(GOOD, f"{why}, but a great deal against your earlier checks{when}", days)
        if close:
            return _make(GOOD, f"{why}{when} — {haul} fares usually rise inside "
                               f"~{rise // 7} weeks, so book soon", days)
        if move == "rising":
            return _make(GOOD, f"{why} and rising over your last checks", days)
        return _make(WAIT, f"{why}{when} — no rush; the watch alerts you when it drops", days)

    # high
    if last_minute:
        return _make(BUY, f"{why}, but {_days(days)} waiting rarely helps", days)
    falling = " and falling" if move == "falling" else ""
    deadline = f" — but don't wait past ~{rise // 7} weeks out" if close else " — wait for a dip"
    return _make(WAIT, f"{why}{falling}{when}{deadline}", days)


# --- building the signals -------------------------------------------------------

def days_until(depart: str | date | None, today: date) -> int | None:
    if isinstance(depart, date):
        return (depart - today).days
    try:
        return (date.fromisoformat(str(depart or "")[:10]) - today).days
    except ValueError:
        return None


def long_haul(origin: str, destination: str) -> bool:
    """Distance band of the route (country hubs, app/airports.py); unknown = short."""
    a = airports.airport(origin) or airports.resolve(origin)
    b = airports.airport(destination) or airports.resolve(destination)
    if a is None or b is None:
        return False
    km = airports.distance_km(a, b)
    return km is not None and km >= LONG_HAUL_KM


def for_search(price: float | None, currency: str, depart: str, today: date,
               insights: PriceInsights | None, bag_offset: float = 0.0,
               origin: str = "", destination: str = "") -> Advice | None:
    """A search's best deal: Google's insights (when they are in the same
    currency) shifted by the deal's bag fees. None without a price."""
    if price is None:
        return None
    lo = hi = None
    lvl = ""
    if insights is not None and insights.currency == currency:
        if insights.typical_low is not None and insights.typical_high is not None:
            lo, hi = insights.typical_low + bag_offset, insights.typical_high + bag_offset
        lvl = insights.price_level
    return advise(Signals(price=price, currency=currency, days_out=days_until(depart, today),
                          typical_low=lo, typical_high=hi, price_level=lvl,
                          long_haul=long_haul(origin, destination)))


def for_watch(stats: WatchStats | None, recent: list[float], depart: str | date | None, today: date,
              origin: str = "", destination: str = "", price: float | None = None,
              prior_low: float | None = None) -> Advice | None:
    """A watch's latest best (or `price`): its stats (Google's range on basis
    google, else the typical of its own runs once there are enough), its trend
    and the days to its best fare's departure. `prior_low` is the 30-day low
    before this price; without it the stats' low counts only when the price
    equals it. None before the first comparable run."""
    if stats is None:
        return None
    price = price if price is not None else stats.last_best
    if price is None:
        return None
    depth = stats.basis_runs + stats.history_points
    typical = stats.typical if depth >= TYPICAL_MIN_DEPTH and stats.basis != "legacy" else None
    low = prior_low if prior_low is not None else (stats.low_30d if depth >= TYPICAL_MIN_DEPTH else None)
    return advise(Signals(price=price, currency=stats.currency, days_out=days_until(depart, today),
                          typical_low=stats.typical_low, typical_high=stats.typical_high, typical=typical,
                          low_30d=low, recent=tuple(recent), long_haul=long_haul(origin, destination),
                          score=stats.score))


def today_of(now: datetime | date | None) -> date:
    if now is None:
        return date.today()
    return now.date() if isinstance(now, datetime) else now
