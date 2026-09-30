"""Pydantic models shared by the agent, storage, and API layers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .currency import normalize as _normalize_currency


TripType = Literal["round_trip", "one_way"]
CabinClass = Literal["economy", "premium_economy", "business", "first"]
PriceConfidence = Literal["verified", "estimated", "from_price"]
BookingLinkKind = Literal["airline", "google_flights", "skyscanner", "kayak", "momondo", "other"]


class Passengers(BaseModel):
    adults: int = Field(default=1, ge=1, le=9, description="Travellers aged 12+")
    children: int = Field(default=0, ge=0, le=8, description="Travellers aged 2-11")
    infants: int = Field(default=0, ge=0, le=4, description="Under 2, on lap")

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants


class SearchParams(BaseModel):
    """Structured search criteria. They shape the agent's search AND the
    pre-filled booking links, so they are persisted with every stored deal.
    Every field has a default, so old rows/requests without them stay valid."""

    trip_type: TripType = "round_trip"
    passengers: Passengers = Field(default_factory=Passengers)
    cabin_class: CabinClass = "economy"
    max_stops: int | None = Field(default=None, ge=0, le=3, description="None = any, 0 = direct only")
    cabin_bag: bool = Field(default=False, description="Prices must include a 10kg cabin trolley bag")
    checked_bag: bool = Field(default=False, description="Prices must include a 20kg+ checked bag")
    # Optional structured date window; dates_hint (free text) stays the primary/fallback input.
    depart_from: date | None = Field(default=None, description="Earliest departure date")
    depart_to: date | None = Field(default=None, description="Latest departure date")
    nights_min: int | None = Field(default=None, ge=0, le=60, description="Min trip length (round trip)")
    nights_max: int | None = Field(default=None, ge=0, le=60, description="Max trip length (round trip)")
    max_price: float | None = Field(default=None, gt=0, description="Price ceiling for the whole party, in `currency`")
    currency: str = Field(default="USD", description="Preferred currency for quoted prices")
    nearby_airports: bool = Field(default=False, description="Also consider airports near origin/destination")

    @field_validator("currency")
    @classmethod
    def _iso_currency_param(cls, v: str) -> str:
        return _normalize_currency(v)


class BookingLink(BaseModel):
    """A purchase/lookup link for a deal, pre-filled with the search criteria where the site supports it."""

    label: str = Field(description="Button text, e.g. 'Wizz Air', 'Google Flights'")
    url: str
    kind: BookingLinkKind = "other"
    prefilled: list[str] = Field(
        default_factory=list,
        description="Which criteria the URL pre-fills: subset of 'route','dates','passengers','cabin','bags','stops'",
    )


class FlightDeal(BaseModel):
    """A single flight deal found on the web."""

    origin: str = Field(description="Origin city or airport code, e.g. 'TLV'")
    destination: str = Field(description="Destination city or airport code, e.g. 'LON'")
    airline: str = Field(description="Operating airline, or 'multiple' if mixed")
    price: float = Field(description="Total round-trip (or one-way) price as a number")
    currency: str = Field(description="ISO currency code of the price, e.g. 'USD'")
    depart_date: str = Field(description="Departure date or date range, e.g. '2026-09-14' or 'mid September'")
    return_date: str = Field(default="", description="Return date if round trip, empty for one-way")
    duration: str = Field(default="", description="Flight duration, e.g. '5h 20m'")
    stops: int = Field(default=0, description="Number of stops (0 = direct)")
    booking_url: str = Field(default="", description="URL where this fare can be booked or tracked")
    source_url: str = Field(default="", description="URL of the page where the deal was found")
    notes: str = Field(default="", description="Anything relevant: baggage, fare class, deal expiry, caveats")
    # --- richer fields (all optional so older rows stay valid) ---
    origin_airport: str = Field(default="", description="IATA code of the departure airport, e.g. 'TLV'")
    destination_airport: str = Field(default="", description="IATA code of the arrival airport, e.g. 'LTN'")
    depart_time: str = Field(default="", description="Outbound local departure time HH:MM, if known")
    arrive_time: str = Field(default="", description="Outbound local arrival time HH:MM, if known")
    return_depart_time: str = Field(default="", description="Return leg local departure time HH:MM, if known")
    flight_numbers: str = Field(default="", description="e.g. 'W6 2328 / W6 2327'")
    airline_code: str = Field(default="", description="IATA airline code of the main carrier, e.g. 'W6'")
    price_per_person: float | None = Field(default=None, description="Price for one adult incl. requested bags")
    price_confidence: PriceConfidence = Field(
        default="estimated",
        description="verified = exact fare seen for these dates; estimated = computed/approximate; from_price = 'from X' teaser",
    )
    booking_links: list[BookingLink] = Field(default_factory=list, description="Built by booking.build_links, not by the LLM")

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, v: str) -> str:
        # The extractor sometimes emits a symbol ("₪") instead of an ISO code;
        # normalizing here covers fresh extractions AND old rows read back.
        return _normalize_currency(v)


class FlightDealList(BaseModel):
    """Structured-output wrapper: the list of deals extracted from research notes."""

    deals: list[FlightDeal] = Field(description="All distinct flight deals found, cheapest first")


class StoredDeal(FlightDeal):
    """A deal as returned from the database."""

    id: int
    search_query: str
    found_at: datetime
    times_seen: int = Field(default=1, description="How many search runs found this exact deal")
    first_found_at: datetime | None = Field(default=None, description="When this deal was first seen (set when times_seen > 1)")
    search_params: SearchParams | None = Field(default=None, description="Criteria of the run that found it (None for legacy rows)")


class HistoryPoint(BaseModel):
    """Best price found for a route in one search run."""

    found_at: datetime
    price: float
    deals: int = Field(description="How many deals that run found for the route")


class RouteHistory(BaseModel):
    route: str = Field(description="Requested route, e.g. 'TLV -> London'")
    currency: str
    points: list[HistoryPoint] = Field(description="Chronological, oldest first")


class WatchCreate(SearchParams):
    """A route the user wants monitored for price drops."""

    origin: str
    destination: str
    dates_hint: str = "anytime in the next 3 months"
    max_price: float = Field(gt=0, description="Alert when a deal is at or below this price")
    currency: str = Field(default="USD", description="Currency the threshold is expressed in")
    direct_only: bool = Field(default=False, description="Legacy alias for max_stops=0")

    @model_validator(mode="after")
    def _direct_alias(self):
        if self.direct_only and self.max_stops is None:
            self.max_stops = 0
        self.direct_only = self.max_stops == 0
        return self


class Watch(WatchCreate):
    id: int
    active: bool = True
    created_at: datetime
    last_run_at: datetime | None = None
    last_best_price: float | None = None


def _criteria_flags(p: SearchParams) -> list[str]:
    """Human-readable non-default criteria, in label order. Each flag is one
    comma-free token so labels stay splittable on ', '."""
    flags: list[str] = []
    if p.depart_from and p.depart_to:
        flags.append(f"depart {p.depart_from.isoformat()} to {p.depart_to.isoformat()}")
    elif p.depart_from:
        flags.append(f"depart from {p.depart_from.isoformat()}")
    elif p.depart_to:
        flags.append(f"depart by {p.depart_to.isoformat()}")
    if p.trip_type == "round_trip" and (p.nights_min is not None or p.nights_max is not None):
        lo, hi = p.nights_min, p.nights_max
        if lo is not None and hi is not None:
            flags.append(f"{lo} nights" if lo == hi else f"{lo}-{hi} nights")
        elif lo is not None:
            flags.append(f"{lo}+ nights")
        else:
            flags.append(f"up to {hi} nights")
    if p.trip_type == "one_way":
        flags.append("one-way")
    pax = p.passengers
    if (pax.adults, pax.children, pax.infants) != (1, 0, 0):
        party = [f"{pax.adults} adult{'s' if pax.adults != 1 else ''}"]
        if pax.children:
            party.append(f"{pax.children} child{'ren' if pax.children != 1 else ''}")
        if pax.infants:
            party.append(f"{pax.infants} infant{'s' if pax.infants != 1 else ''}")
        flags.append(" + ".join(party))
    if p.cabin_class != "economy":
        flags.append(p.cabin_class.replace("_", " "))
    if p.max_stops is not None and p.max_stops > 0:
        flags.append(f"max {p.max_stops} stop{'s' if p.max_stops != 1 else ''}")
    if p.nearby_airports:
        flags.append("nearby airports")
    if p.max_price is not None:
        flags.append(f"up to {p.max_price:.0f} {p.currency}")
    return flags


def search_label(origin: str, destination: str, dates_hint: str,
                 direct_only: bool, cabin_bag: bool, checked_bag: bool,
                 params: SearchParams | None = None) -> str:
    """Canonical stored label for a search run, e.g.
    'TLV -> Prague (October, direct only, cabin bag, checked 20kg bag)', or with
    params: 'TLV -> Prague (October, one-way, 2 adults + 1 child, business,
    max 1 stop, cabin bag)'.

    The flags live inside the parens so route parsing (split on ' (') and the
    frontend's re-run parser stay in sync. The legacy flags (direct only, cabin
    bag, checked 20kg bag) always come last and in that order, so old parsers
    that peel them off the tail keep working; newer criteria sit between the
    dates text and those. `params`, when given, supersedes the three flags."""
    flags = ""
    if params is not None:
        direct_only = params.max_stops == 0
        cabin_bag, checked_bag = params.cabin_bag, params.checked_bag
        for flag in _criteria_flags(params):
            flags += f", {flag}"
    if direct_only:
        flags += ", direct only"
    if cabin_bag:
        flags += ", cabin bag"
    if checked_bag:
        flags += ", checked 20kg bag"
    return f"{origin} -> {destination} ({dates_hint}{flags})"


class SearchRequest(SearchParams):
    origin: str = Field(description="City, airport code, or comma-separated list")
    destination: str = Field(description="City, airport, country/region ('Greece'), comma list, or 'anywhere'")
    dates_hint: str = Field(
        default="anytime in the next 3 months",
        description="Free-text date preference, e.g. 'first two weeks of October', 'a weekend in November'",
    )
    direct_only: bool = Field(default=False, description="Legacy alias for max_stops=0")

    @model_validator(mode="after")
    def _direct_alias(self):
        if self.direct_only and self.max_stops is None:
            self.max_stops = 0
        self.direct_only = self.max_stops == 0
        return self

    def params(self) -> SearchParams:
        return SearchParams(**self.model_dump(include=set(SearchParams.model_fields)))
