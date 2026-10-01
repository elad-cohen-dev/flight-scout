"""Travelpayouts / Aviasales Data API: free, cached fares from users' searches.

Docs: https://travelpayouts.github.io/slate/ (v1/v2) and the Aviasales Data API
v3 reference on support.travelpayouts.com. We use v3 `prices_for_dates`:

    GET https://api.travelpayouts.com/aviasales/v3/prices_for_dates
        ?origin=TLV&destination=PRG&departure_at=2026-11[-03]&return_at=...
        &one_way=false&direct=false&currency=usd&sorting=price&limit=100
    X-Access-Token: <TRAVELPAYOUTS_TOKEN>
    -> {"success": true, "currency": "usd", "data": [{origin, destination,
        origin_airport, destination_airport, price, airline, flight_number,
        departure_at, return_at, transfers, return_transfers, duration,
        duration_to, duration_back, link}, ...]}

- `departure_at` takes a day or a month, so one call is a whole month's
  calendar; leaving out `destination` is an explore search ("anywhere").
- Prices are for ONE adult, economy, no bags, and come from a cache of other
  users' searches (hours to days old). We multiply by the paying travellers,
  add ~10% per lap infant, and always mark them 'estimated' ('from_price' when
  the sighting is over a week old).
- `link` is an Aviasales search path ending in the party ('/search/MAD2807BCN26081'
  = 1 adult); we rewrite that to the searched party. The booking URL is
  https://www.aviasales.com + link + marker=<TRAVELPAYOUTS_MARKER> (affiliate).
- Free; we still count calls (provider_usage) and cache responses for 6h.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

from .. import airlines, storage
from ..models import FlightDeal, SearchParams
from .base import FareQuery, ProviderError, ProviderResult, cached_get, fmt_minutes, paying, store

logger = logging.getLogger(__name__)

PROVIDER = "travelpayouts"
SOURCE = "aviasales"
URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
SITE = "https://www.aviasales.com"
CACHE_TTL_SECONDS = 6 * 3600
LIMIT = 100
INFANT_FACTOR = 0.1  # lap infants pay roughly 10% of the adult fare
TEASER_AFTER = timedelta(days=7)  # a sighting this old is a "from" price, not a fare


def token() -> str:
    return os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()


def marker() -> str:
    return os.environ.get("TRAVELPAYOUTS_MARKER", "").strip()


def configured() -> bool:
    return bool(token())


def used_this_month() -> int:
    return storage.provider_calls(PROVIDER)


def request_params(query: FareQuery, *, explore: bool = False) -> dict:
    p = query.params
    out = {
        "origin": query.origin,
        "departure_at": query.depart_date,
        "one_way": "false" if p.trip_type == "round_trip" else "true",
        "direct": "true" if p.max_stops == 0 else "false",
        "currency": p.currency.lower(),
        "sorting": "price",
        "unique": "false",
        "limit": LIMIT,
    }
    if not explore:
        out["destination"] = query.destination
    if query.return_date and p.trip_type == "round_trip":
        out["return_at"] = query.return_date
    return out


def fetch(params: dict) -> tuple[dict, bool]:
    """(response, from_cache). Raises ProviderError on failure."""
    if not configured():
        raise ProviderError("travelpayouts: TRAVELPAYOUTS_TOKEN not set")
    data, cached = cached_get(PROVIDER, URL, params, {"X-Access-Token": token()})
    if cached:
        return data, True
    if data.get("success") is False:
        raise ProviderError(f"travelpayouts: {str(data.get('error') or 'request failed')[:200]}")
    store(PROVIDER, params, data, CACHE_TTL_SECONDS)
    return data, False


# --- mapping -----------------------------------------------------------------

def _split(value: str) -> tuple[str, str]:
    """'2026-11-03T06:35:00+02:00' -> ('2026-11-03', '06:35') — local time at the airport."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?", value or "")
    return (m.group(1), m.group(2) or "") if m else ("", "")


# '/search/MAD2807BCN26081?t=...': origin, DDMM, destination, [DDMM,] then the
# party — adults, plus children and infants digits when any ('201').
_SEARCH_PATH = re.compile(r"^(/search/[A-Z]{3}\d{4}[A-Z]{3}(?:\d{4})?)(\d{1,3})(?=\?|$)")


def _with_party(link: str, params: SearchParams | None) -> str:
    """Rewrite the party in an Aviasales search path (cached rows are for 1 adult)."""
    if params is None:
        return link
    pax = params.passengers
    party = f"{pax.adults}" + (f"{pax.children}{pax.infants}" if pax.children or pax.infants else "")
    return _SEARCH_PATH.sub(lambda m: m.group(1) + party, link, count=1)


def booking_url(link: str, params: SearchParams | None = None) -> str:
    if not link:
        return ""
    link = _with_party(link, params)
    url = link if link.startswith("http") else SITE + ("" if link.startswith("/") else "/") + link
    if marker() and "marker=" not in url:
        url += ("&" if "?" in url else "?") + f"marker={marker()}"
    return url


def _seen_at(row: dict) -> str:
    raw = row.get("found_at") or ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return ""
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def map_row(row: dict, currency: str, params: SearchParams, now: datetime | None = None) -> FlightDeal | None:
    """One prices_for_dates row (a 1-adult fare) -> a party-total FlightDeal."""
    try:
        per_adult = float(row.get("price"))
    except (TypeError, ValueError):
        return None
    if per_adult <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    pax = params.passengers
    total = per_adult * paying(params) + per_adult * INFANT_FACTOR * pax.infants
    dep_date, dep_time = _split(row.get("departure_at", ""))
    ret_date, ret_time = _split(row.get("return_at", "")) if params.trip_type == "round_trip" else ("", "")
    code = str(row.get("airline") or "").strip().upper()
    carrier = airlines.by_code(code)
    number = str(row.get("flight_number") or "").strip()
    seen_at = _seen_at(row)
    old = bool(seen_at) and now - datetime.fromisoformat(seen_at) > TEASER_AFTER

    notes = [f"Cached Aviasales price {per_adult:,.0f} {currency} per adult"
             + (f" x {pax.total} travellers" if pax.total > 1 else "")
             + (" (lap infants ~10%)" if pax.infants else "")]
    if params.trip_type == "round_trip" and ret_date:
        notes.append("round trip")
        if row.get("return_transfers") is not None:
            back = int(row["return_transfers"])
            notes.append("return direct" if back == 0 else f"return {back} stop{'s' if back != 1 else ''}")
    if params.cabin_bag or params.checked_bag:
        notes.append("bags NOT included — add the airline's bag fees")
    notes.append("price from other travellers' recent searches; confirm on the booking site")

    url = booking_url(str(row.get("link") or ""), params)
    return FlightDeal(
        origin=str(row.get("origin_airport") or row.get("origin") or ""),
        destination=str(row.get("destination_airport") or row.get("destination") or ""),
        origin_airport=str(row.get("origin_airport") or ""),
        destination_airport=str(row.get("destination_airport") or ""),
        airline=carrier.name if carrier else code,
        airline_code=code,
        flight_numbers=f"{code} {number}" if code and number.isdigit() else "",
        price=round(total, 2),
        price_per_person=round(per_adult, 2),
        currency=currency,
        depart_date=dep_date,
        return_date=ret_date,
        depart_time=dep_time,
        return_depart_time=ret_time,
        duration=fmt_minutes(row.get("duration_to") or row.get("duration")),
        stops=int(row.get("transfers") or 0),
        booking_url=url,
        source_url=url,
        notes="; ".join(notes) + ".",
        price_confidence="from_price" if old else "estimated",
        source=SOURCE,
        seen_at=seen_at,
    )


def map_response(data: dict, params: SearchParams, now: datetime | None = None) -> list[FlightDeal]:
    currency = str(data.get("currency") or params.currency).upper()
    rows = data.get("data") or []
    if isinstance(rows, dict):  # grouped endpoints key rows by date
        rows = list(rows.values())
    return [d for d in (map_row(r, currency, params, now) for r in rows if isinstance(r, dict)) if d]


def search(query: FareQuery, *, explore: bool = False) -> ProviderResult:
    """Cached fares for a day or a whole month (`query.depart_date` YYYY-MM-DD
    or YYYY-MM); `explore=True` drops the destination (cheapest anywhere)."""
    data, cached = fetch(request_params(query, explore=explore))
    return ProviderResult(deals=map_response(data, query.params), paid_calls=0, cached=cached)
