"""SerpApi Google Flights (engine=google_flights): exact, live, dated fares.

Docs: https://serpapi.com/google-flights-api (+ /google-flights-booking-options).

- Prices are what Google Flights shows for the requested party: the TOTAL for
  every passenger passed in (adults/children/infants), in `currency`. On a round
  trip (type=1) the outbound list already carries the ROUND-TRIP total; the
  return leg's times need a second call with that itinerary's departure_token,
  which we only make on request (it costs a search).
- `bags=N` makes Google add carry-on fees for N passengers to the price; there
  is no checked-bag filter, so checked bags stay a note + 'estimated'.
- Quota: free plan 250 searches/month. We count every uncached call in
  provider_usage and stop at SERPAPI_MONTHLY_BUDGET (default 230, headroom for
  probes/manual use). SerpApi itself serves an identical query within 1h from
  its cache for free; our own 1h response cache makes that explicit.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from .. import storage
from ..models import FlightDeal, PriceInsights, SearchParams
from .base import (
    FareQuery, ProviderError, ProviderResult, cached_get, fmt_minutes, paying, store,
)

logger = logging.getLogger(__name__)

PROVIDER = "serpapi"
SOURCE = "google_flights"
URL = "https://serpapi.com/search"
CACHE_TTL_SECONDS = 3600
DEFAULT_MONTHLY_BUDGET = 230
MAX_ITINERARIES = 8  # per query; the rest are pricier variants of the same flights

TRAVEL_CLASS = {"economy": "1", "premium_economy": "2", "business": "3", "first": "4"}
# SerpApi `stops`: 0 any, 1 nonstop, 2 one stop or fewer, 3 two stops or fewer.
_NO_RESULTS = re.compile(r"hasn'?t returned any results|no results", re.IGNORECASE)
_BAG_WORDS = re.compile(r"\b(bag|bags|baggage|carry-on|carry on|checked)\b", re.IGNORECASE)


def api_key() -> str:
    return os.environ.get("SERPAPI_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key())


def monthly_budget() -> int:
    try:
        return max(0, int(os.environ.get("SERPAPI_MONTHLY_BUDGET", DEFAULT_MONTHLY_BUDGET)))
    except ValueError:
        return DEFAULT_MONTHLY_BUDGET


def used_this_month() -> int:
    return storage.provider_calls(PROVIDER)


def remaining() -> int:
    """Live searches left this month under our budget (0 when unconfigured)."""
    if not configured():
        return 0
    return max(0, monthly_budget() - used_this_month())


def _stops_param(max_stops: int | None) -> str:
    if max_stops is None:
        return "0"
    return str(min(max_stops, 2) + 1)


def request_params(query: FareQuery) -> dict:
    """SerpApi query parameters (without api_key) for a FareQuery."""
    p = query.params
    pax = p.passengers
    out = {
        "engine": "google_flights",
        "departure_id": query.origin,
        "arrival_id": query.destination,
        "outbound_date": query.depart_date,
        "type": "1" if query.return_date else "2",
        "travel_class": TRAVEL_CLASS[p.cabin_class],
        "adults": pax.adults,
        "currency": p.currency,
        "hl": "en",
        "stops": _stops_param(p.max_stops),
    }
    if query.return_date:
        out["return_date"] = query.return_date
    if pax.children:
        out["children"] = pax.children
    if pax.infants:
        out["infants_on_lap"] = pax.infants
    if p.cabin_bag:
        # One carry-on per traveller with a seat; Google adds the fees to the price.
        out["bags"] = pax.adults + pax.children
    return out


def _trim(data: dict) -> dict:
    """What we keep of a response: itineraries, insights (minus the long
    history), the Google Flights URL, and any error."""
    insights = dict(data.get("price_insights") or {})
    insights.pop("price_history", None)
    meta = data.get("search_metadata") or {}
    return {
        "search_metadata": {k: meta.get(k) for k in ("id", "status", "google_flights_url", "created_at")},
        "best_flights": data.get("best_flights") or [],
        "other_flights": data.get("other_flights") or [],
        "price_insights": insights,
        # Present only on booking_token requests.
        "booking_options": data.get("booking_options") or [],
        "baggage_prices": data.get("baggage_prices") or {},
        "error": data.get("error", ""),
    }


def fetch(params: dict) -> tuple[dict, bool]:
    """(trimmed response, from_cache) for SerpApi `params`. Raises
    BudgetExhausted / ProviderError. A "no results" answer is a valid, cached,
    empty response."""
    if not configured():
        raise ProviderError("serpapi: SERPAPI_API_KEY not set")
    data, cached = cached_get(PROVIDER, URL, {**params, "api_key": api_key()}, budget_left=remaining)
    if cached:
        return data, True
    error = str(data.get("error") or "")
    if error and not _NO_RESULTS.search(error):
        raise ProviderError(f"serpapi: {error[:200]}")
    trimmed = _trim(data)
    store(PROVIDER, {**params, "api_key": ""}, trimmed, CACHE_TTL_SECONDS)
    return trimmed, False


# --- mapping -----------------------------------------------------------------

def _split_time(value: str) -> tuple[str, str]:
    """'2026-11-03 06:35' -> ('2026-11-03', '06:35')."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})", value or "")
    if not m:
        return "", ""
    hh, mm = m.group(2).split(":")
    return m.group(1), f"{int(hh):02d}:{mm}"


def _unique(values) -> list:
    return list(dict.fromkeys(v for v in values if v))


def _flight_prefix(number: str) -> str:
    m = re.match(r"\s*([A-Z0-9]{2})\s*\d", number or "")
    return m.group(1) if m else ""


def _layover_text(layover: dict) -> str:
    where = layover.get("id") or layover.get("name") or "?"
    text = f"via {where} {fmt_minutes(layover.get('duration'))}".strip()
    if layover.get("overnight"):
        text += " (overnight)"
    return text


def _bag_notes(itinerary: dict, params: SearchParams) -> list[str]:
    notes = [e for e in itinerary.get("extensions") or [] if _BAG_WORDS.search(str(e))]
    for seg in itinerary.get("flights") or []:
        notes += [e for e in seg.get("extensions") or [] if _BAG_WORDS.search(str(e))]
    notes = _unique(notes)
    if params.cabin_bag:
        notes.append("Price includes carry-on bag fees (Google Flights bags filter)")
    if params.checked_bag:
        notes.append("Checked 20kg bag NOT included in this fare — add the airline's bag fee")
    return notes


def map_itinerary(itinerary: dict, query: FareQuery, google_url: str, seen_at: str) -> FlightDeal | None:
    """One best_flights/other_flights entry -> FlightDeal (None when unpriced)."""
    params = query.params
    segments = itinerary.get("flights") or []
    price = itinerary.get("price")
    if not segments or not isinstance(price, (int, float)) or price <= 0:
        return None
    first, last = segments[0], segments[-1]
    dep_date, dep_time = _split_time((first.get("departure_airport") or {}).get("time", ""))
    arr_date, arr_time = _split_time((last.get("arrival_airport") or {}).get("time", ""))
    origin = (first.get("departure_airport") or {}).get("id", "") or query.origin
    destination = (last.get("arrival_airport") or {}).get("id", "") or query.destination
    airlines = _unique(s.get("airline", "") for s in segments)
    numbers = _unique(s.get("flight_number", "") for s in segments)
    codes = _unique(_flight_prefix(n) for n in numbers)
    layovers = itinerary.get("layovers") or []
    stops = len(layovers) if layovers else len(segments) - 1
    party = paying(params)
    round_trip = bool(query.return_date)

    notes = [f"Google Flights {'round-trip' if round_trip else 'one-way'} fare, total for "
             f"{params.passengers.total} traveller{'s' if params.passengers.total != 1 else ''}"]
    if layovers:
        notes.append("Connects " + "; ".join(_layover_text(l) for l in layovers))
    if arr_date and dep_date and arr_date != dep_date:
        notes.append(f"Arrives {arr_date}")
    if any(s.get("often_delayed_by_over_30_min") for s in segments):
        notes.append("Often delayed by 30+ min")
    notes += _bag_notes(itinerary, params)

    return FlightDeal(
        origin=origin,
        destination=destination,
        origin_airport=origin,
        destination_airport=destination,
        airline=airlines[0] if len(airlines) == 1 else " + ".join(airlines),
        airline_code=codes[0] if len(codes) == 1 else "",
        flight_numbers=" / ".join(numbers),
        price=float(price),
        price_per_person=round(float(price) / party, 2),
        currency=params.currency,
        depart_date=dep_date or query.depart_date,
        return_date=query.return_date,
        depart_time=dep_time,
        arrive_time=arr_time,
        duration=fmt_minutes(itinerary.get("total_duration")),
        stops=max(stops, 0),
        booking_url=google_url,
        source_url=google_url,
        notes=". ".join(notes) + ".",
        price_confidence="estimated" if params.checked_bag else "verified",
        source=SOURCE,
        seen_at=seen_at,
    )


def map_insights(data: dict, query: FareQuery) -> PriceInsights | None:
    raw = data.get("price_insights") or {}
    if not raw:
        return None
    typical = raw.get("typical_price_range") or []
    lo, hi = (typical + [None, None])[:2] if isinstance(typical, list) else (None, None)
    return PriceInsights(
        route=f"{query.origin}>{query.destination}",
        currency=query.params.currency,
        lowest_price=raw.get("lowest_price"),
        price_level=str(raw.get("price_level") or ""),
        typical_low=lo,
        typical_high=hi,
        depart_date=query.depart_date,
        return_date=query.return_date,
    )


def map_response(data: dict, query: FareQuery, seen_at: str | None = None) -> tuple[list[FlightDeal], dict]:
    """(deals, departure_token by deal index) for a search response."""
    seen_at = seen_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    google_url = (data.get("search_metadata") or {}).get("google_flights_url") or ""
    itineraries = [*(data.get("best_flights") or []), *(data.get("other_flights") or [])]
    itineraries.sort(key=lambda it: it.get("price") if isinstance(it.get("price"), (int, float)) else 1e12)
    deals: list[FlightDeal] = []
    tokens: dict[int, str] = {}
    for it in itineraries:
        deal = map_itinerary(it, query, google_url, seen_at)
        if deal is None:
            continue
        if it.get("departure_token"):
            tokens[len(deals)] = it["departure_token"]
        deals.append(deal)
        if len(deals) >= MAX_ITINERARIES:
            break
    return deals, tokens


def search(query: FareQuery) -> ProviderResult:
    """Live Google Flights fares for one exact date (pair)."""
    data, cached = fetch(request_params(query))
    created = (data.get("search_metadata") or {}).get("created_at") or ""
    seen_at = _created_iso(created) if cached else None
    deals, tokens = map_response(data, query, seen_at)
    return ProviderResult(deals=deals, insights=map_insights(data, query),
                          paid_calls=0 if cached else 1, cached=cached, departure_tokens=tokens)


def _created_iso(created: str) -> str | None:
    """SerpApi's '2023-10-02 05:42:48 UTC' -> ISO, for cached responses."""
    try:
        return datetime.strptime(created, "%Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def with_return_leg(deal: FlightDeal, query: FareQuery, departure_token: str) -> tuple[FlightDeal, int]:
    """Fill a round-trip deal's return times/flight numbers from the return
    flights its departure_token lists (one more search; the cheapest return
    whose price equals the outbound's round-trip total is the match). Returns
    (deal, paid calls)."""
    data, cached = fetch({**request_params(query), "departure_token": departure_token})
    returns = [*(data.get("best_flights") or []), *(data.get("other_flights") or [])]
    match = min(returns, key=lambda it: (abs((it.get("price") or 1e12) - deal.price), it.get("price") or 1e12),
                default=None)
    if not match or not match.get("flights"):
        return deal, 0 if cached else 1
    segs = match["flights"]
    _, back_time = _split_time((segs[0].get("departure_airport") or {}).get("time", ""))
    numbers = _unique(s.get("flight_number", "") for s in segs)
    price = match.get("price")
    update = {
        "return_depart_time": back_time,
        "flight_numbers": " / ".join(_unique([*deal.flight_numbers.split(" / "), *numbers])),
    }
    if isinstance(price, (int, float)) and price > 0:
        update["price"] = float(price)
        update["price_per_person"] = round(float(price) / paying(query.params), 2)
    return deal.model_copy(update=update), 0 if cached else 1


def booking_options(query: FareQuery, booking_token: str) -> list[dict]:
    """Booking options for an itinerary (one more search): seller, price,
    Google's redirect request and baggage prices. Not used by the search flow
    yet — the probe script exercises it so the format can be verified."""
    data, _ = fetch({**request_params(query), "booking_token": booking_token})
    out = []
    for option in data.get("booking_options") or []:
        part = option.get("together") or option.get("departing") or {}
        request = part.get("booking_request") or {}
        out.append({
            "book_with": part.get("book_with", ""),
            "price": part.get("price"),
            "option_title": part.get("option_title", ""),
            "baggage_prices": part.get("baggage_prices") or [],
            "url": request.get("url", ""),
            "post_data": request.get("post_data", ""),
            "separate_tickets": bool(option.get("separate_tickets")),
        })
    return out
