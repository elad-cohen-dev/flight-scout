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


def search_label(origin: str, destination: str, dates_hint: str,
                 direct_only: bool, cabin_bag: bool, checked_bag: bool) -> str:
    """Canonical stored label for a search run, e.g.
    'TLV -> Prague (October, direct only, cabin bag, checked 20kg bag)'.
    The flags live inside the parens so route parsing (split on ' (') and the
    frontend's re-run parser stay in sync."""
    flags = ""
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
