"""Explore mode: "anywhere from TLV" — the cheapest destinations Google sees.

    explore(req) -> ExploreResult      # one SerpApi search, or free when cached
    quote(req)   -> ExploreQuote       # what explore(req) would cost; no network

One Google Travel Explore call (engine=google_travel_explore, no arrival_id)
answers a list of destinations from `origin`, each with Google's cheapest
date pair for the `month` (1-12 within the next six months; 0 = any of them)
and trip length (`travel_duration` 1 weekend / 2 one week / 3 two weeks), the
flight price for the whole party, airline, stops, duration, coordinates and a
thumbnail. Those prices are Google's own aggregate, not a bookable itinerary,
so destinations come back as source google_explore / "estimated"; the UI's
"Check this fare live" runs a normal exact-date search to confirm one.

Cost & caching: one call = one search of the SerpApi quota (the same monthly
budget as everything else). Responses are cached for CACHE_TTL_SECONDS (6h,
vs 1h for live fares): Explore prices are a daily-ish aggregate rather than
seat inventory, nothing is bought off them without a live confirmation, and
browsing the map, changing the price cap or the region filter, or coming back
later the same day should not spend another search. Only what changes Google's
answer is sent (origin, month, trip length, one-way, stops, cabin, party,
carry-on bags, currency); max_price, region and country are applied here
after the fetch, so they share one cached response.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field

from . import airports, booking, storage
from .models import BookingLink, CabinClass, Passengers, SearchParams, TripType
from .providers import serpapi
from .providers.base import BudgetExhausted, ProviderError, cache_key, paying

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 6 * 3600
MAX_DESTINATIONS = 60
HORIZON_MONTHS = 6  # Explore's `month` only reaches the next six months

TravelDuration = Literal["weekend", "week", "two_weeks"]
DURATION_PARAM = {"weekend": serpapi.WEEKEND, "week": serpapi.ONE_WEEK, "two_weeks": serpapi.TWO_WEEKS}
# A return Google left out is estimated from the trip length: Fri -> Sun, 7 or 14 nights.
DURATION_NIGHTS = {"weekend": 2, "week": 7, "two_weeks": 14}

Region = Literal["europe", "middle_east", "africa", "asia", "americas", "oceania"]
REGION_LABELS: dict[str, str] = {
    "europe": "Europe", "middle_east": "Middle East", "africa": "Africa",
    "asia": "Asia", "americas": "Americas", "oceania": "Oceania",
}
_REGION_COUNTRIES = {
    "europe": "AL AT BA BE BG BY CH CY CZ DE DK EE ES FI FR GB GI GR HR HU IE IS IT LT LU LV MD ME MK MT "
              "NL NO PL PT RO RS RU SE SI SK TR UA XK GE AM AZ",
    "middle_east": "AE BH IL JO KW LB OM QA SA",
    "africa": "EG MA TN ZA KE ET TZ MU SC NG GH RW UG NA ZW MG SN RE",
    "asia": "IN LK MV NP TH VN KH LA MM MY SG ID PH CN HK MO TW JP KR MN UZ KZ KG",
    "americas": "US CA MX CU DO JM BS BB AW CW SX PR PA CR GT SV CO VE EC PE BO BR AR CL PY UY",
    "oceania": "AU NZ FJ PF",
}
COUNTRY_REGION = {cc: region for region, codes in _REGION_COUNTRIES.items() for cc in codes.split()}


class ExploreRequest(BaseModel):
    origin: str = Field(default="TLV", description="Airport or city ('TLV', 'London [LON]')")
    month: int = Field(default=0, ge=0, le=12, description="1-12 within the next six months; 0 = any of them")
    travel_duration: TravelDuration = Field(default="week", description="Round trips: weekend, 1 week or 2 weeks")
    trip_type: TripType = "round_trip"
    max_stops: int | None = Field(default=None, ge=0, le=2, description="None = any, 0 = direct only")
    passengers: Passengers = Field(default_factory=Passengers)
    cabin_class: CabinClass = "economy"
    cabin_bag: bool = Field(default=False, description="Prices include one carry-on per traveller (Google bags filter)")
    currency: str = "USD"
    # Applied after the fetch (they share one cached response):
    max_price: float | None = Field(default=None, gt=0, description="Party total ceiling, in `currency`")
    region: Region | None = None
    country: str = Field(default="", description="ISO code or name; only destinations in it")
    cached_only: bool = Field(default=False, description="Answer only from the cache (404 otherwise): never spends a search")

    def params(self) -> SearchParams:
        return SearchParams(trip_type=self.trip_type, passengers=self.passengers, cabin_class=self.cabin_class,
                            max_stops=self.max_stops, cabin_bag=self.cabin_bag, currency=self.currency)


class ExploreDestination(BaseModel):
    city: str
    country: str = ""
    country_code: str = ""
    region: str = Field(default="", description="One of REGION_LABELS' keys, '' when unknown")
    destination_id: str = Field(default="", description="Google's city kgmid, e.g. '/m/04jpl'")
    airport: str = Field(description="IATA code Google priced, e.g. 'LTN'")
    search_destination: str = Field(description="What 'Check live' searches: 'London [LON]' (all airports) or 'Paphos [PFO]'")
    lat: float | None = None
    lon: float | None = None
    thumbnail: str = ""
    price: float = Field(description="Total for the party, in `currency`")
    price_per_person: float
    currency: str
    depart_date: str
    return_date: str = ""
    return_estimated: bool = Field(default=False, description="Google gave no return date; derived from the trip length")
    nights: int | None = None
    airline: str = ""
    airline_code: str = ""
    stops: int = 0
    duration: str = ""
    duration_minutes: int | None = None
    source: Literal["google_explore"] = "google_explore"
    price_confidence: Literal["estimated"] = "estimated"
    google_url: str = ""
    booking_links: list[BookingLink] = Field(default_factory=list)


class ExploreQuote(BaseModel):
    configured: bool
    cached: bool = Field(description="An identical explore is cached: running it is free")
    cost: int = Field(description="Live searches running it spends (0 or 1)")
    left: int = Field(description="Live searches left this month under our budget")
    budget: int


class ExploreResult(BaseModel):
    origin: str
    month: int
    travel_duration: TravelDuration
    trip_type: TripType
    currency: str
    destinations: list[ExploreDestination]
    total: int = Field(description="Destinations Google returned, before max_price/region/country")
    cached: bool
    live_searches: int = Field(description="Searches this call spent (0 or 1)")
    left: int
    fetched_at: str = Field(description="When Google answered (ISO, UTC)")
    expires_at: str = Field(description="When the cached answer goes stale and a re-run costs a search")
    messages: list[str] = Field(default_factory=list)


class ExploreError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --- request ---------------------------------------------------------------------

def _origin(origin: str) -> tuple[str, str]:
    """(Explore's departure_id, the origin's code) for the origin text: a
    metro's kgmid or comma list, else the airport code — the same resolution
    as a normal search."""
    from .engine import resolve_end  # engine imports a lot; keep this module light to import

    end = resolve_end(origin)
    if end is None:
        raise ExploreError(422, f"Explore needs one airport or city to fly from — couldn't place “{origin}”.")
    return serpapi.explore_place(end.label, end.serp), end.label


def months_ahead(today: date) -> list[int]:
    """The calendar months Explore can answer, from this one: [10, 11, 12, 1, 2, 3]."""
    return [(today.month - 1 + i) % 12 + 1 for i in range(HORIZON_MONTHS)]


def request_params(req: ExploreRequest, today: date | None = None) -> dict:
    """SerpApi parameters (without api_key) for an open-destination explore."""
    today = today or date.today()
    if req.month and req.month not in months_ahead(today):
        raise ExploreError(422, "Google Explore only covers the next six months — pick one of them, or 'any'.")
    params = req.params()
    out = serpapi.explore_request_params(_origin(req.origin)[0], "", params, req.month,
                                         DURATION_PARAM[req.travel_duration])
    out.pop("arrival_id", None)  # no destination = the destinations list
    return out


# --- mapping -----------------------------------------------------------------------

@lru_cache(maxsize=1)
def _metro_of() -> dict[str, airports.Place]:
    return {code: m for m in airports.index().metros.values() for code in m.members}


@lru_cache(maxsize=1)
def _country_codes() -> dict[str, str]:
    out = {}
    for code, info in airports.index().countries.items():
        for name in (info.get("name", ""), *info.get("aliases", [])):
            out[airports._key(name)] = code
    return out


def _country_code(airport: str, country: str) -> str:
    place = airports.airport(airport)
    if place is not None:
        return place.country
    return _country_codes().get(airports._key(country), "")


def _search_destination(city: str, airport: str) -> str:
    """'London [LON]' when the priced airport belongs to a metro (the live check
    then covers every airport of the city), else 'City [CODE]'."""
    metro = _metro_of().get(airport)
    if metro is not None and re.fullmatch(r"[A-Z]{3}", metro.code):
        return f"{metro.city} [{metro.code}]"
    place = airports.airport(airport)
    return f"{(place.city if place else '') or city} [{airport}]"


def _coord(value, lo: float, hi: float) -> float | None:
    return float(value) if isinstance(value, (int, float)) and lo <= value <= hi else None


def map_destination(d: dict, req: ExploreRequest, origin_code: str, explore_url: str,
                    seen_at: str) -> ExploreDestination | None:
    """One `destinations` entry -> ExploreDestination (None when unpriced or undated)."""
    params = req.params()
    code = str((d.get("destination_airport") or {}).get("code") or "").upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        return None
    round_trip = req.trip_type == "round_trip"
    start, end = str(d.get("start_date") or ""), str(d.get("end_date") or "")
    estimated = False
    if round_trip and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        try:
            end = (date.fromisoformat(start) + timedelta(days=DURATION_NIGHTS[req.travel_duration])).isoformat()
            estimated = True
        except ValueError:
            return None
    deal = serpapi._explore_deal(
        origin=origin_code, destination=code, price=d.get("flight_price"), start=start, end=end,
        airline=str(d.get("airline") or ""), code=str(d.get("airline_code") or ""),
        minutes=d.get("flight_duration"), stops=d.get("number_of_stops"),
        url=str(d.get("link") or explore_url), params=params, seen_at=seen_at)
    if deal is None:
        return None
    city = str(d.get("name") or "") or code
    country = str(d.get("country") or "")
    cc = _country_code(code, country)
    gps = d.get("gps_coordinates") or {}
    minutes = d.get("flight_duration")
    nights = None
    if round_trip:
        nights = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return ExploreDestination(
        city=city,
        country=country or (airports.index().countries.get(cc) or {}).get("name", ""),
        country_code=cc,
        region=COUNTRY_REGION.get(cc, ""),
        destination_id=str(d.get("destination_id") or ""),
        airport=code,
        search_destination=_search_destination(city, code),
        lat=_coord(gps.get("latitude"), -90, 90),
        lon=_coord(gps.get("longitude"), -180, 180),
        thumbnail=str(d.get("thumbnail") or "") if str(d.get("thumbnail") or "").startswith("https://") else "",
        price=deal.price,
        price_per_person=deal.price_per_person or round(deal.price / paying(params), 2),
        currency=deal.currency,
        depart_date=deal.depart_date,
        return_date=deal.return_date,
        return_estimated=estimated,
        nights=nights,
        airline=deal.airline,
        airline_code=deal.airline_code,
        stops=deal.stops,
        duration=deal.duration,
        duration_minutes=int(minutes) if isinstance(minutes, (int, float)) and minutes > 0 else None,
        google_url=deal.booking_url,
        booking_links=booking.build_links(deal, params),
    )


def map_destinations(data: dict, req: ExploreRequest, origin_code: str, seen_at: str) -> list[ExploreDestination]:
    """Every priced destination, cheapest first, one per city."""
    explore_url = (data.get("search_metadata") or {}).get("google_travel_explore_url") or ""
    out: list[ExploreDestination] = []
    for d in data.get("destinations") or []:
        if not isinstance(d, dict):
            continue
        try:
            mapped = map_destination(d, req, origin_code, explore_url, seen_at)
        except Exception:  # one odd entry never sinks the list
            logger.exception("explore: unmappable destination")
            continue
        if mapped is not None:
            out.append(mapped)
    out.sort(key=lambda x: x.price)
    seen: set[str] = set()
    unique = []
    for x in out:
        key = x.destination_id or x.search_destination
        if key not in seen:
            seen.add(key)
            unique.append(x)
    return unique[:MAX_DESTINATIONS]


def apply_filters(destinations: list[ExploreDestination], req: ExploreRequest) -> list[ExploreDestination]:
    """max_price / region / country — after the fetch, so they cost nothing."""
    out = destinations
    if req.max_price:
        out = [x for x in out if x.price <= req.max_price]
    if req.region:
        out = [x for x in out if x.region == req.region]
    if req.country.strip():
        want = req.country.strip()
        code = want.upper() if re.fullmatch(r"[A-Za-z]{2}", want) else _country_codes().get(airports._key(want), "")
        out = [x for x in out if (x.country_code == code if code else airports._key(x.country) == airports._key(want))]
    return out


# --- entry points ------------------------------------------------------------------

def _is_cached(params: dict) -> bool:
    try:
        return storage.cache_get(cache_key(serpapi.PROVIDER, params)) is not None
    except Exception:
        logger.exception("explore: cache read failed")
        return False


def quote(req: ExploreRequest, today: date | None = None) -> ExploreQuote:
    """What running `req` costs right now: 0 when an identical explore is
    cached, else 1 search. Never touches the network."""
    configured = serpapi.configured()
    cached = configured and _is_cached(request_params(req, today))
    return ExploreQuote(configured=configured, cached=cached, cost=0 if cached or not configured else 1,
                        left=serpapi.remaining(), budget=serpapi.monthly_budget())


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def explore(req: ExploreRequest, today: date | None = None, now: datetime | None = None) -> ExploreResult:
    """The cheapest destinations from `req.origin` (one search unless cached).
    Raises ExploreError: 503 not configured, 409 budget used up, 502 Google
    failed, 404 not cached (cached_only)."""
    if not serpapi.configured():
        raise ExploreError(503, "Explore needs live Google search (SERPAPI_API_KEY) — it isn't configured.")
    params = request_params(req, today)
    if req.cached_only and not _is_cached(params):
        raise ExploreError(404, "Not cached — running this explore costs 1 live search.")
    try:
        data, cached = serpapi.fetch(params, serpapi._trim_explore, CACHE_TTL_SECONDS)
    except BudgetExhausted:
        raise ExploreError(409, f"This month's live Google budget ({serpapi.monthly_budget()} searches) is used up — "
                                "Explore is back next month.") from None
    except ProviderError as exc:
        logger.warning("serpapi explore (anywhere) failed: %s", exc)
        raise ExploreError(502, "Google Explore is unavailable right now — try again in a bit.") from None
    now = now or datetime.now(timezone.utc)
    created = (data.get("search_metadata") or {}).get("created_at") or ""
    fetched = (serpapi._created_iso(created) if cached else None) or _iso(now)
    origin_code = _origin(req.origin)[1]
    everything = map_destinations(data, req, origin_code, fetched)
    shown = apply_filters(everything, req)
    messages = []
    if not everything:
        messages.append("Google returned no destinations for these criteria — try 'any month', a longer trip or allowing stops.")
    elif not shown:
        messages.append("Nothing matches your price cap / region — loosen them to see the rest (free, same results).")
    if any(x.return_estimated for x in shown):
        messages.append("Some return dates are estimated from the trip length — the live check confirms them.")
    try:
        expires = _iso(datetime.fromisoformat(fetched) + timedelta(seconds=CACHE_TTL_SECONDS))
    except ValueError:
        expires = _iso(now + timedelta(seconds=CACHE_TTL_SECONDS))
    return ExploreResult(origin=origin_code, month=req.month, travel_duration=req.travel_duration,
                         trip_type=req.trip_type, currency=req.params().currency, destinations=shown,
                         total=len(everything), cached=cached, live_searches=0 if cached else 1,
                         left=serpapi.remaining(), fetched_at=fetched, expires_at=expires, messages=messages)
