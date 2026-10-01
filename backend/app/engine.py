"""Fare-search orchestration: real fare APIs first, the LLM agent for the rest.

    search(origin, destination, dates_hint, params) -> SearchOutcome(deals, meta)

Routing (each step only when its key is configured):

- **No provider keys** -> the LLM agent (agent.find_deals), unchanged.
- **Exact route, exact dates** (both ends resolve to one airport/city, and the
  departure — plus the return on round trips — is one known day) -> SerpApi
  Google Flights live; if it can't answer (no key, budget used up, error),
  the Aviasales cache for those dates.
- **Exact route, flexible dates** -> the Aviasales calendar (free, one call per
  month of the window) picks the cheapest date pairs; the top `live_limit`
  are confirmed live with SerpApi. Cached fares for other dates are shown too,
  badged as cached. Without the calendar (or when it has nothing), Google
  Travel Explore picks them instead: one search per month of the window (at
  most MAX_EXPLORE_CALLS; see explore_months), then the cheapest pair(s) are
  confirmed on Google Flights — MAX_FLEX_CREDITS live searches at most, ~2
  typically. Explore prices for unconfirmed pairs are shown as estimates.
- **Region / "anywhere" / comma lists / nearby airports** -> the LLM agent,
  seeded with the cheapest Aviasales explore results as leads (and those that
  provably match the destination are shown as cached results).
- **Nothing matched** -> the LLM agent as the last resort (when an LLM key is set).

Every provider deal goes through agent.postprocess (normalize + criteria
filters), exactly like the agent's own. Progress streams through `on_event`
like the agent's, and every emission can raise SearchCancelled.

Watches use `find_for_watch`, which pre-screens with the free cache (or, without
it, with Google Travel Explore) and only spends a live Google Flights search
when it might matter (see `prescreen_decision`). `watch_credits` estimates
what a watch costs per check and per month.
"""

from __future__ import annotations

import logging
import os
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import agent, airports, alerts, llm_usage, storage
from .booking import _MONTH_RE, _parse_date, _parse_month
from .currency import convert
from .models import (
    FlightDeal, ProviderUsage, SearchMeta, SearchParams, UsageReport, Watch, WatchCredits,
)
from .providers import serpapi, travelpayouts
from .providers.base import BudgetExhausted, FareQuery, ProviderError

logger = logging.getLogger(__name__)

CONFIRM_LIVE = 2          # flexible dates: cheapest cached date pairs re-checked live per search
WATCH_CONFIRM_LIVE = 1    # ...per watch run
CALENDAR_PICKS = 3        # cheapest cached date pairs considered
MAX_CACHED_SHOWN = 6      # cached fares shown next to live ones
MAX_LEADS = 8             # explore sightings handed to the LLM
MAX_MONTHS = 3            # calendar months fetched for one window
DEFAULT_WINDOW_DAYS = 90  # "anytime" = the next three months
PRESCREEN_MARGIN = 0.15   # live check when the cached best is within 15% of firing
WEEKLY_LIVE_DAYS = 7      # ...or at least this often per watch
MAX_EXPLORE_CALLS = 2     # Google Travel Explore searches per flexible search / watch run
MAX_FLEX_CREDITS = 3      # ...plus the Google Flights confirmations, at most
EXPLORE_HORIZON_MONTHS = 6  # Explore's `month` only reaches this far ahead
WATCH_RUNS_PER_MONTH = 30   # the daily sweep (.github/workflows/watch-flights.yml)

SOURCE_ORDER = ["google_flights", "google_explore", "aviasales", "web"]
_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass
class SearchOutcome:
    deals: list[FlightDeal]
    meta: SearchMeta


def llm_configured() -> bool:
    try:
        agent._provider()
        return True
    except RuntimeError:
        return False


def _web_agent(origin: str, destination: str, dates_hint: str, params: SearchParams, on_event,
               meta: SearchMeta, leads: str = "") -> list[FlightDeal]:
    """agent.find_deals, refused (no deals, a message) once the monthly AI
    budget is spent; at 80% of it the search runs with a warning."""
    try:
        _, warning = llm_usage.check_budget()
    except llm_usage.AIBudgetExceeded as exc:
        meta.messages.append(str(exc))
        _emitter(on_event)(str(exc))
        return []
    if warning:
        meta.messages.append(warning)
    return agent.find_deals(origin, destination, dates_hint, params=params, on_event=on_event, leads=leads)


def _emitter(on_event):
    def emit(text: str, kind: str = "phase") -> None:
        if on_event:
            on_event({"kind": kind, "text": text})
    return emit


# --- places & dates -------------------------------------------------------------

@dataclass(frozen=True)
class _End:
    """One end of a route as each provider wants it."""
    label: str      # 'PRG', 'LON'
    serp: str       # SerpApi departure_id/arrival_id: an airport or a comma list of airports
    tp: str         # Travelpayouts origin/destination: an IATA airport or city code


def resolve_end(text: str) -> _End | None:
    """The single place a provider can search for `text`, or None for lists,
    regions, countries and unknown names (those go to the LLM)."""
    if agent.is_flexible(text):
        return None
    # The autocomplete writes "City [CODE]" ("London [LON]", "Istanbul [IST/SAW]").
    # The bracket is the user's explicit pick — resolve that, not the city text:
    # "London [LON]" used to resolve to London City alone, so a direct TLV→London
    # search found nothing on Google Flights and fell back to the web agent.
    picked = re.search(r"\[([A-Z]{3}(?:/[A-Z]{3})*)\]\s*$", text or "")
    if picked:
        codes = picked.group(1).split("/")
        if len(codes) > 1:  # a multi-airport city without a metro code
            return _End(codes[0], ",".join(codes), codes[0])
        text = codes[0]
    place = airports.resolve(text)
    if place is None:
        return None
    members = [m for m in place.members if re.fullmatch(r"[A-Z]{3}", m)]
    if place.kind == "metro":
        if not members:
            return None
        return _End(place.iata or members[0], ",".join(members), place.iata or members[0])
    if not place.iata:
        return None
    return _End(place.iata, place.iata, place.iata)


@dataclass
class DatePlan:
    pairs: list[tuple[str, str]]  # exact (depart, return or '') — at most one from the request
    start: date
    end: date

    @property
    def months(self) -> list[str]:
        out, cur = [], date(self.start.year, self.start.month, 1)
        while cur <= self.end and len(out) < MAX_MONTHS:
            out.append(cur.strftime("%Y-%m"))
            cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        return out


def _month_bounds(hint: str, today: date) -> tuple[date, date] | None:
    if re.search(r"\bnext month\b", hint or "", re.IGNORECASE):
        y, m = (today.year + (today.month == 12), today.month % 12 + 1)
    else:
        ym = _parse_month(hint, today)
        if ym is None:
            return None
        y, m = ym
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


_DAY_MENTION = (rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\b"
                rf"|{_MONTH_RE}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b)")
_BOUND = re.compile(rf"\b(after|from|starting(?:\s+(?:on|from))?|since|not before|before|until|till|by"
                    rf"|no later than)\s+(?:the\s+)?({_DAY_MENTION})", re.IGNORECASE)
_RANGE = re.compile(rf"\b(?:from|between)\s+({_DAY_MENTION})\s*(?:-|–|to|and|until|till|through|thru)\s*"
                    rf"({_DAY_MENTION})", re.IGNORECASE)
_LATER = {"after": 1, "before": -1}  # strict bounds move one day; the rest are inclusive
_START_WORDS = ("after", "from", "starting", "since", "not before")


def _hint_window(hint: str, today: date) -> tuple[date | None, date | None, tuple[date, date] | None]:
    """Window words in free text: (start from 'after/from <day>', end from
    'before/until/by <day>' or 'from <day> to <day>', the span of the bare
    months named — 'October - November' is Oct 1 to Nov 30). Day mentions
    don't count as bare months. Years follow the span's current occurrence: on
    Nov 2, 'October - November after October 11th' is still Nov 2026."""
    bare = re.sub(_DAY_MENTION, " ", hint or "", flags=re.IGNORECASE)
    months = []
    for m in re.finditer(rf"\b{_MONTH_RE}\b(?:\s+\d{{4}})?", bare, re.IGNORECASE):
        ym = _parse_month(m.group(0), today)
        if ym and ym not in months:
            months.append(ym)
    span = None
    if len(months) >= 2:
        (y1, m1), (y2, m2) = months[0], months[-1]
        if not re.search(r"\d{4}", bare):  # an ongoing span: anchor on its last month
            y1 = y2 if m1 <= m2 else y2 - 1
        elif (y2, m2) < (y1, m1):
            y2 += 1
        span = date(y1, m1, 1), date(y2, m2, monthrange(y2, m2)[1])

    def day_of(text: str) -> date | None:
        day = _parse_date(text, today)
        if day and span and not re.search(r"\d{4}", text):
            for year in (span[0].year, span[1].year):
                inside = date(year, day.month, min(day.day, monthrange(year, day.month)[1]))
                if span[0] <= inside <= span[1]:
                    return inside
        return day

    start = end = None
    for m in _BOUND.finditer(hint or ""):
        word, day = m.group(1), day_of(m.group(2))
        if day is None:
            continue
        day += timedelta(days=_LATER.get(word.lower(), 0))
        if word.lower().startswith(_START_WORDS):
            start = max(start, day) if start else day
        else:
            end = min(end, day) if end else day
    for m in _RANGE.finditer(hint or ""):  # "from Nov 3 to Nov 20": the departure window
        first, last = day_of(m.group(1)), day_of(m.group(m.lastindex))
        start = start or first
        if last and (start is None or last >= start):
            end = min(end, last) if end else last
    return start, end, span


def plan_dates(dates_hint: str, params: SearchParams, today: date) -> DatePlan:
    """Exact date pair when the request names one, plus the departure window
    the calendar should scan otherwise. Free text like 'October - November
    after October 11th' is a window (Oct 12 to Nov 30), not a date."""
    rt = params.trip_type == "round_trip"
    hint = dates_hint or ""
    win_start, win_end, span = _hint_window(hint, today)
    windowed = bool(win_start or win_end)
    depart = ret = None
    if params.depart_from and params.depart_from == params.depart_to:
        depart = params.depart_from
    elif not params.depart_from and not params.depart_to and not windowed:
        iso = sorted(set(_ISO.findall(hint)))
        if iso:
            depart = agent.parse_iso_date(iso[0])
            if rt and len(iso) >= 2:
                ret = agent.parse_iso_date(iso[1])
        else:
            depart = _parse_date(hint, today)
    if rt and depart and params.nights_min is not None and params.nights_min == params.nights_max:
        ret = depart + timedelta(days=params.nights_min)
    if ret and depart and ret < depart:
        ret = None

    pairs = []
    if depart and depart >= today and (not rt or ret):
        pairs = [(depart.isoformat(), ret.isoformat() if ret else "")]

    bounds = span or _month_bounds(re.sub(_DAY_MENTION, " ", hint, flags=re.IGNORECASE) if windowed else hint,
                                       today)
    start = (params.depart_from or depart or win_start
             or (bounds[0] if bounds else today + timedelta(days=1)))
    end = (params.depart_to or depart or win_end
           or (bounds[1] if bounds and bounds[1] >= start else start + timedelta(days=DEFAULT_WINDOW_DAYS)))
    start = max(start, today)
    return DatePlan(pairs=pairs, start=start, end=max(end, start))


# --- provider steps ----------------------------------------------------------------

def _cached_fares(o: _End, d: _End, plan: DatePlan, params: SearchParams, emit, meta: SearchMeta,
                  exact: bool) -> list[FlightDeal]:
    """Aviasales cache: the exact date pair, or every month of the window."""
    queries = ([FareQuery(o.tp, d.tp, plan.pairs[0][0], plan.pairs[0][1], params)] if exact
               else [FareQuery(o.tp, d.tp, month, "", params) for month in plan.months])
    deals: list[FlightDeal] = []
    for q in queries:
        emit(f"Aviasales cache: {o.label} → {d.label}, {q.depart_date}", "search")
        try:
            deals += travelpayouts.search(q).deals
        except ProviderError as exc:
            logger.warning("travelpayouts failed: %s", exc)
            meta.messages.append("Cached fares (Aviasales) are unavailable right now.")
            break
    lo, hi = plan.start.isoformat(), plan.end.isoformat()
    return [x for x in deals if not x.depart_date or lo <= x.depart_date <= hi]


def _cheapest_pairs(deals: list[FlightDeal], params: SearchParams, n: int,
                    today: date) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for x in agent.postprocess(deals, params, today):
        pair = (x.depart_date, x.return_date if params.trip_type == "round_trip" else "")
        if not _ISO.fullmatch(pair[0]) or (params.trip_type == "round_trip" and not _ISO.fullmatch(pair[1])):
            continue
        if pair not in pairs:
            pairs.append(pair)
        if len(pairs) >= n:
            break
    return pairs


def _return_legs() -> int:
    try:
        return max(0, int(os.environ.get("SERPAPI_RETURN_LEGS", "0")))
    except ValueError:
        return 0


def _live_fares(o: _End, d: _End, pairs: list[tuple[str, str]], params: SearchParams, emit,
                meta: SearchMeta) -> tuple[list[FlightDeal], set[tuple[str, str]]]:
    """SerpApi for each date pair; returns (deals, the pairs actually answered)."""
    deals: list[FlightDeal] = []
    answered: set[tuple[str, str]] = set()
    if pairs:
        emit("Checking Google Flights…")
    for dep, ret in pairs:
        emit(f"Google Flights: {o.label} → {d.label} {dep}" + (f" – {ret}" if ret else ""), "search")
        query = FareQuery(o.serp, d.serp, dep, ret, params)
        try:
            result = serpapi.search(query)
        except BudgetExhausted:
            meta.messages.append(
                f"This month's live Google Flights budget ({serpapi.monthly_budget()} searches) is used up — "
                "showing cached fares instead.")
            break
        except ProviderError as exc:
            logger.warning("serpapi failed: %s", exc)
            meta.messages.append("Google Flights is unavailable right now — showing other sources.")
            break
        meta.live_searches += result.paid_calls
        if meta.price_insights is None and result.insights is not None:
            meta.price_insights = result.insights
        answered.add((dep, ret))
        found = result.deals
        for i in range(min(_return_legs(), len(found)) if ret else 0):
            token = result.departure_tokens.get(i)
            if not token or serpapi.remaining() <= 0:
                break
            emit(f"Google Flights: return flights for {found[i].flight_numbers or found[i].airline}", "search")
            try:
                found[i], calls = serpapi.with_return_leg(found[i], query, token)
                meta.live_searches += calls
            except ProviderError as exc:
                logger.warning("serpapi return leg failed: %s", exc)
                break
        deals += found
    return deals, answered


# --- Google Travel Explore: the cheapest dates without the calendar -------------

def explore_months(plan: DatePlan, today: date) -> list[int]:
    """Explore `month` values (1-12) covering the departure window: one per
    month it touches inside Explore's six-month horizon. More than
    MAX_EXPLORE_CALLS months -> [0] (Explore's "any of the next six months";
    its pair is kept only when it falls inside the window)."""
    out, cur = [], date(plan.start.year, plan.start.month, 1)
    while cur <= plan.end:
        ahead = (cur.year - today.year) * 12 + cur.month - today.month
        if 0 <= ahead < EXPLORE_HORIZON_MONTHS:
            out.append(cur.month)
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out if len(out) <= MAX_EXPLORE_CALLS else [0]


def explore_duration(dates_hint: str, params: SearchParams) -> int:
    """Explore's travel_duration for the trip length asked: a weekend (≤3
    nights or 'weekend' in the text), two weeks (≥11), else one week."""
    if re.search(r"\bweekends?\b", dates_hint or "", re.IGNORECASE):
        return serpapi.WEEKEND
    lo, hi = params.nights_min, params.nights_max
    target = (lo + hi) / 2 if lo is not None and hi is not None else (lo if lo is not None else hi)
    if target is None:
        return serpapi.ONE_WEEK
    return serpapi.WEEKEND if target <= 3 else serpapi.TWO_WEEKS if target >= 11 else serpapi.ONE_WEEK


def _fit_pair(deal: FlightDeal, plan: DatePlan, params: SearchParams) -> tuple[tuple[str, str] | None, bool]:
    """(the date pair to confirm for an Explore sighting, whether it is Explore's
    own pair). Departures outside the window give None; a trip length outside
    nights_min..nights_max keeps the departure and moves the return to the
    nearest allowed length (Explore only knows weekend / 1 week / 2 weeks)."""
    try:
        out = date.fromisoformat(deal.depart_date)
    except ValueError:
        return None, False
    if not plan.start <= out <= plan.end:
        return None, False
    if params.trip_type != "round_trip":
        return (deal.depart_date, ""), True
    try:
        back = date.fromisoformat(deal.return_date)
    except ValueError:
        return None, False
    nights = (back - out).days
    lo = params.nights_min if params.nights_min is not None else 0
    hi = params.nights_max if params.nights_max is not None else 60
    fitted = min(max(nights, lo), max(lo, hi))
    if fitted == nights:
        return (deal.depart_date, deal.return_date), True
    return (deal.depart_date, (out + timedelta(days=fitted)).isoformat()), False


def _explore_dates(o: _End, d: _End, dates_hint: str, plan: DatePlan, params: SearchParams, emit,
                   meta: SearchMeta, today: date) -> tuple[list[FlightDeal], list[tuple[str, str]], int]:
    """Google Travel Explore over the window: (sightings that fit the request —
    shown as estimates, date pairs to confirm — cheapest first, paid calls)."""
    origin = serpapi.explore_place(o.label, o.serp)
    destination = serpapi.explore_place(d.label, d.serp)
    members = tuple(d.serp.split(","))
    duration = explore_duration(dates_hint, params)
    found: list[FlightDeal] = []
    paid = 0
    months = explore_months(plan, today)
    if months:
        emit("Finding the cheapest dates on Google…")
    for month in months:
        when = date(today.year + (month < today.month), month, 1).strftime("%b %Y") if month else "next 6 months"
        emit(f"Google Explore: {o.label} → {d.label}, {when}", "search")
        try:
            result = serpapi.explore(origin, destination, params, month, duration, members)
        except BudgetExhausted:
            meta.messages.append(
                f"This month's live Google budget ({serpapi.monthly_budget()} searches) is used up — "
                "the cheapest dates couldn't be looked up.")
            break
        except ProviderError as exc:
            logger.warning("serpapi explore failed: %s", exc)
            meta.messages.append("Google's cheapest-dates search is unavailable right now.")
            break
        paid += result.paid_calls
        found += result.deals
    meta.live_searches += paid

    shown: list[FlightDeal] = []
    ranked: list[tuple[float, tuple[str, str]]] = []
    for deal in sorted(found, key=lambda x: x.price):
        pair, own = _fit_pair(deal, plan, params)
        if pair is None:
            continue
        if own:
            shown.append(deal)
        if pair not in [p for _, p in ranked]:
            ranked.append((deal.price, pair))
    shown = agent.postprocess(shown, params, today)
    return shown, [p for _, p in ranked], paid


def _country_code(text: str) -> str:
    key = airports._key(text)
    for code, info in airports.index().countries.items():
        if key in {airports._key(info.get("name", "")), *(airports._key(a) for a in info.get("aliases", []))}:
            return code
    return ""


def _destination_filter(destination: str):
    """Predicate over explore deals that provably match `destination`, or None
    when we can't tell (regions) — then explore results are leads only."""
    text = destination.strip()
    if re.fullmatch(r"(?i)anywhere|everywhere", text):
        return lambda deal: True
    country = _country_code(text)
    if country:
        def in_country(deal: FlightDeal) -> bool:
            place = airports.airport(deal.destination_airport) or airports.resolve(deal.destination)
            return place is not None and place.country == country
        return in_country
    parts = [p for p in re.split(r"\s*,\s*|\s+or\s+", text, flags=re.IGNORECASE) if p]
    places = [airports.resolve(p) for p in parts]
    if len(parts) > 1 and all(places):
        return lambda deal: any(airports.contains(p, deal.destination_airport) or p.code == deal.destination
                                for p in places)
    return None


def _lead_line(deal: FlightDeal) -> str:
    when = deal.depart_date + (f" – {deal.return_date}" if deal.return_date else "")
    per = deal.price_per_person or deal.price
    stops = "direct" if deal.stops == 0 else f"{deal.stops} stop(s)"
    return f"- {deal.destination_airport or deal.destination}: ~{per:,.0f} {deal.currency} per adult, {when}, {deal.airline}, {stops}"


def _explore(origin: str, destination: str, dates_hint: str, params: SearchParams, on_event,
             meta: SearchMeta, today: date) -> list[FlightDeal]:
    emit = _emitter(on_event)
    shown: list[FlightDeal] = []
    leads: list[FlightDeal] = []
    o = resolve_end(origin)
    if travelpayouts.configured() and o is not None:
        plan = plan_dates(dates_hint, params, today)
        emit("Checking cached fares (Aviasales) for cheap destinations…")
        found: list[FlightDeal] = []
        for month in plan.months[:2]:
            emit(f"Aviasales explore: {o.label} → anywhere, {month}", "search")
            try:
                found += travelpayouts.search(FareQuery(o.tp, "", month, "", params), explore=True).deals
            except ProviderError as exc:
                logger.warning("travelpayouts explore failed: %s", exc)
                break
        keep = _destination_filter(destination)
        candidates = [x for x in found if keep is None or keep(x)]
        leads = agent.postprocess(candidates, params, today)[:MAX_LEADS]
        if keep is not None:
            shown = leads
    web: list[FlightDeal] = []
    if llm_configured():
        web = _web_agent(origin, destination, dates_hint, params, on_event, meta,
                         leads="\n".join(_lead_line(x) for x in leads))
    elif not shown:
        meta.messages.append("Region and 'anywhere' searches need an LLM key (ANTHROPIC_API_KEY or OPENAI_API_KEY).")
    return agent.postprocess(web + shown, params, today) if shown else web


# --- entry points ------------------------------------------------------------------

def _sources(deals: list[FlightDeal], meta: SearchMeta) -> list[str]:
    seen = {x.source for x in deals if x.source} | set(meta.sources)
    return [s for s in SOURCE_ORDER if s in seen]


def search(origin: str, destination: str, dates_hint: str, params: SearchParams, on_event=None,
           *, live_limit: int = CONFIRM_LIVE, today: date | None = None) -> SearchOutcome:
    """Find deals for a request; see the module docstring for the routing."""
    today = today or date.today()
    meta = SearchMeta()
    emit = _emitter(on_event)
    live_ok, cache_ok = serpapi.configured(), travelpayouts.configured()

    if not live_ok and not cache_ok:
        deals = _web_agent(origin, destination, dates_hint, params, on_event, meta)
        meta.sources = ["web"]
        return SearchOutcome(deals, meta)

    o, d = resolve_end(origin), resolve_end(destination)
    if o is None or d is None or params.nearby_airports:
        deals = _explore(origin, destination, dates_hint, params, on_event, meta, today)
        meta.sources = _sources(deals, meta)
        emit(f"Found {len(deals)} deals.")
        return SearchOutcome(deals, meta)

    plan = plan_dates(dates_hint, params, today)
    exact = bool(plan.pairs)
    cached: list[FlightDeal] = []
    pairs = plan.pairs
    if not exact and cache_ok:
        emit("Checking cached fares (Aviasales) for the cheapest dates…")
        cached = _cached_fares(o, d, plan, params, emit, meta, exact=False)
        pairs = _cheapest_pairs(cached, params, CALENDAR_PICKS, today)
    if not exact and live_ok and not pairs:
        # No calendar (or it had nothing): Google Explore picks the dates, then
        # Google Flights confirms them — MAX_FLEX_CREDITS live searches at most.
        explored, pairs, paid = _explore_dates(o, d, dates_hint, plan, params, emit, meta, today)
        cached += explored
        live_limit = max(1, min(live_limit, MAX_FLEX_CREDITS - paid))

    live: list[FlightDeal] = []
    answered: set[tuple[str, str]] = set()
    # Flexible dates nobody could date leave no pairs: the agent handles those.
    if live_ok and pairs:
        live, answered = _live_fares(o, d, pairs[:live_limit], params, emit, meta)

    if exact and not live and cache_ok:
        emit("Checking cached fares (Aviasales)…")
        cached = _cached_fares(o, d, plan, params, emit, meta, exact=True)

    rt = params.trip_type == "round_trip"
    others = [x for x in cached if (x.depart_date, x.return_date if rt else "") not in answered]
    others = agent.postprocess(others, params, today)[:MAX_CACHED_SHOWN]
    deals = agent.postprocess(live + others, params, today) if (live or others) else []

    if not deals:
        if llm_configured():
            emit("No fare-API results for this search — searching the live web…")
            deals = _web_agent(origin, destination, dates_hint, params, on_event, meta)
            meta.sources = ["web"]
        else:
            meta.messages.append("No fares found from the fare APIs, and no LLM key is set for a web search.")
    meta.sources = _sources(deals, meta)
    emit(f"Found {len(deals)} deals.")
    return SearchOutcome(deals, meta)


# --- watches: free pre-screen before a paid live search ----------------------------

def _best_in(deals: list[FlightDeal], currency: str) -> float | None:
    prices = [p for p in (convert(x.price, x.currency, currency) for x in deals) if p is not None]
    return round(min(prices), 2) if prices else None


def prescreen_decision(watch: Watch, cached_best: float | None, prior_rows: list[dict],
                       last_live: datetime | None, live_left: int | None,
                       now: datetime, google: alerts.GoogleHistory | None = None,
                       label: str = "cached") -> tuple[bool, str]:
    """Spend a live search on this watch run? (decision, human-readable reason).

    `live_left` is the SerpApi budget left (None = not configured). Live when:
    no cached fare at all; the cached best is within 15% of the threshold; a
    smart rule would fire if the live price came in 15% under the cached one;
    or the watch hasn't had a live check for a week. The rules compare on the
    same basis as the run itself (`google`: the watch's stored price insights).
    `label` names the pre-screen price in the reason ("cached", "Google Explore"). Pure."""
    cur = watch.currency
    if live_left is None:
        return False, "live search not configured"
    if live_left <= 0:
        return False, "no live searches left this month"
    if cached_best is None:
        return True, f"no {label} fare for this route"
    fmt = alerts.fmt_price
    if watch.alerts.threshold and cached_best <= watch.max_price * (1 + PRESCREEN_MARGIN):
        return True, f"{label} best {fmt(cached_best, cur)} is within 15% of the {fmt(watch.max_price, cur)} threshold"
    prior = alerts.run_bests(prior_rows, cur, alerts.fare_kind(watch))
    hits = [h.rule for h in alerts.evaluate_rules(cached_best * (1 - PRESCREEN_MARGIN), prior, watch.alerts,
                                                  watch.max_price, cur, now, google) if h.rule != "threshold"]
    if hits:
        return True, f"a smart rule may fire ({', '.join(hits)})"
    if last_live is None:
        return True, "first live check for this watch"
    last_live = last_live if last_live.tzinfo else last_live.replace(tzinfo=timezone.utc)
    age = now - last_live
    if age >= timedelta(days=WEEKLY_LIVE_DAYS):
        return True, f"weekly live check (last one {age.days} days ago)"
    days_left = WEEKLY_LIVE_DAYS - age.days
    return False, (f"{label} best {fmt(cached_best, cur)} is over 15% above the {fmt(watch.max_price, cur)} "
                   f"threshold and no rule is close; next live check in {days_left} day{'s' if days_left != 1 else ''}")


def _decide(watch: Watch, best: float | None, prior_rows: list[dict], now: datetime,
            label: str) -> tuple[bool, str]:
    """The pre-screen decision for this run, logged in watch_prescreen."""
    left = serpapi.remaining() if serpapi.configured() else None
    try:
        last_live = storage.last_live_check(watch.id)
    except Exception:
        logger.exception("watch #%s: reading last live check failed", watch.id)
        last_live = None
    try:
        google = alerts.latest_google(storage.watch_price_insights().get(watch.id, []), watch,
                                      watch.currency, now)
    except Exception:
        logger.exception("watch #%s: reading price insights failed", watch.id)
        google = None
    live, reason = prescreen_decision(watch, best, prior_rows, last_live, left, now, google, label)
    logger.info("watch #%s pre-screen: %s — %s", watch.id, "live" if live else f"{label} only", reason)
    try:
        storage.log_prescreen(watch.id, live, reason, best, watch.currency, now)
    except Exception:
        logger.exception("watch #%s: logging the pre-screen failed", watch.id)
    return live, reason


def find_for_watch(watch: Watch, params: SearchParams, prior_rows: list[dict], on_event=None,
                   now: datetime | None = None) -> SearchOutcome:
    """One watch run: a cheap pre-screen, a live Google Flights search only
    when it may matter (decision logged in watch_prescreen), the plain search
    otherwise. The pre-screen is the free Aviasales cache when configured;
    without it, flexible dates use Google Travel Explore (1-2 searches, which
    also pick the date pair to confirm)."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    o, d = resolve_end(watch.origin), resolve_end(watch.destination)
    plan = plan_dates(watch.dates_hint, params, today) if o and d else None
    exact_route = o is not None and d is not None and not params.nearby_airports
    if exact_route and travelpayouts.configured():
        return _watch_via_cache(watch, params, prior_rows, o, d, plan, on_event, now)
    if exact_route and serpapi.configured() and not plan.pairs:
        return _watch_via_explore(watch, params, prior_rows, o, d, plan, on_event, now)
    outcome = search(watch.origin, watch.destination, watch.dates_hint, params, on_event,
                     live_limit=WATCH_CONFIRM_LIVE, today=today)
    outcome.meta.decision = "no pre-screen (cached fares unavailable for this watch)"
    return outcome


def _watch_via_cache(watch, params, prior_rows, o, d, plan, on_event, now) -> SearchOutcome:
    emit = _emitter(on_event)
    meta = SearchMeta()
    emit("Pre-screening with cached fares (free)…")
    cached = agent.postprocess(_cached_fares(o, d, plan, params, emit, meta, exact=bool(plan.pairs)),
                               params, now.date())
    live, reason = _decide(watch, _best_in(cached, watch.currency), prior_rows, now, "cached")
    emit(("Live check: " if live else "Cached fares only: ") + reason)

    if live or not cached:
        outcome = search(watch.origin, watch.destination, watch.dates_hint, params, on_event,
                         live_limit=WATCH_CONFIRM_LIVE, today=now.date())
        outcome.meta.messages = meta.messages + outcome.meta.messages
    else:
        meta.sources = _sources(cached, meta)
        outcome = SearchOutcome(cached, meta)
    outcome.meta.decision = ("live: " if live else "cached: ") + reason
    return outcome


def _watch_via_explore(watch, params, prior_rows, o, d, plan, on_event, now) -> SearchOutcome:
    """Explore is the pre-screen: its best price decides whether the cheapest
    pair is confirmed on Google Flights (verified deals + price insights)."""
    emit = _emitter(on_event)
    today = now.date()
    meta = SearchMeta()
    explored, pairs, _ = _explore_dates(o, d, watch.dates_hint, plan, params, emit, meta, today)
    live, reason = _decide(watch, _best_in(explored, watch.currency), prior_rows, now, "Google Explore")
    if not pairs:
        live = False
    emit(("Live check: " if live else "Google Explore prices only: ") + reason)

    deals: list[FlightDeal] = []
    if live:
        found, answered = _live_fares(o, d, pairs[:WATCH_CONFIRM_LIVE], params, emit, meta)
        rt = params.trip_type == "round_trip"
        others = [x for x in explored if (x.depart_date, x.return_date if rt else "") not in answered]
        deals = agent.postprocess(found + others[:MAX_CACHED_SHOWN], params, today) if (found or others) else []
    elif explored:
        deals = explored
    if not deals:
        # Explore found nothing usable (or the budget ran out): the plain search,
        # whose Explore calls are now cached (free), ends at the web agent.
        outcome = search(watch.origin, watch.destination, watch.dates_hint, params, on_event,
                         live_limit=WATCH_CONFIRM_LIVE, today=today)
        outcome.meta.messages = meta.messages + outcome.meta.messages
        outcome.meta.live_searches += meta.live_searches
        outcome.meta.decision = "explore: " + reason
        return outcome
    meta.sources = _sources(deals, meta)
    meta.decision = ("live: " if live else "explore: ") + reason
    emit(f"Found {len(deals)} deals.")
    return SearchOutcome(deals, meta)


# --- credits: what watches cost --------------------------------------------------

def watch_credits(watch: Watch, today: date | None = None) -> WatchCredits:
    """Most live searches one check of `watch` spends, and per month (daily
    sweep). Exact dates: 1 Google Flights search. Flexible dates: 1
    confirmation, plus the Explore calls when the Aviasales calendar isn't
    configured. Region / 'anywhere' / nearby-airport watches go to the web
    agent: 0. Nothing is live without SerpApi."""
    today = today or date.today()
    per_check = 0
    o, d = resolve_end(watch.origin), resolve_end(watch.destination)
    if serpapi.configured() and o and d and not watch.nearby_airports:
        params = SearchParams(**watch.model_dump(include=set(SearchParams.model_fields)))
        plan = plan_dates(watch.dates_hint, params, today)
        per_check = WATCH_CONFIRM_LIVE
        if not plan.pairs and not travelpayouts.configured():
            per_check = min(MAX_FLEX_CREDITS, len(explore_months(plan, today)) + WATCH_CONFIRM_LIVE)
    per_month = per_check * WATCH_RUNS_PER_MONTH
    if per_check == 0:
        text = "No live searches (cached fares / web search only)"
    else:
        text = (f"Up to {per_check} live search{'es' if per_check != 1 else ''} per check "
                f"(~{per_month}/month)")
    return WatchCredits(per_check=per_check, per_month=per_month, text=text)


def watch_projection(watches: list[Watch], today: date | None = None) -> tuple[int, str]:
    """(live searches the active watches may spend in a month, a warning when
    that exceeds the monthly budget, else '')."""
    total = sum(watch_credits(w, today).per_month for w in watches if w.active)
    budget = serpapi.monthly_budget()
    if serpapi.configured() and total > budget:
        return total, (f"Active watches may use up to ~{total} live searches a month — over the "
                       f"{budget}-search budget. Pause a watch or narrow its dates, or searches "
                       "will fall back to estimates late in the month.")
    return total, ""


def usage_report(now: datetime | None = None) -> UsageReport:
    """Calls this month per provider, for GET /api/usage and the UI meter."""
    now = now or datetime.now(timezone.utc)
    serp_used = storage.provider_calls(serpapi.PROVIDER, now)
    budget = serpapi.monthly_budget()
    projection, warning = watch_projection(storage.list_watches(active_only=True), now.date())
    try:
        research, extract = agent.model_for("research"), agent.model_for("extract")
    except RuntimeError:  # no LLM key (or an unknown FLIGHT_SCOUT_PROVIDER)
        research = extract = ""
    ai = llm_usage.spend(llm_configured(), research, extract, now)
    return UsageReport(month=now.strftime("%Y-%m"), watch_projection=projection, warning=warning, ai=ai, providers=[
        ProviderUsage(provider=serpapi.PROVIDER, configured=serpapi.configured(), used=serp_used,
                      budget=budget, left=max(0, budget - serp_used)),
        ProviderUsage(provider=travelpayouts.PROVIDER, configured=travelpayouts.configured(),
                      used=storage.provider_calls(travelpayouts.PROVIDER, now)),
    ])
