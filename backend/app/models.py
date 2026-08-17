"""Pydantic models shared by the agent, storage, and API layers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .currency import normalize as _normalize_currency


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


class HistoryPoint(BaseModel):
    """Best price found for a route in one search run."""

    found_at: datetime
    price: float
    deals: int = Field(description="How many deals that run found for the route")


class RouteHistory(BaseModel):
    route: str = Field(description="Requested route, e.g. 'TLV -> London'")
    currency: str
    points: list[HistoryPoint] = Field(description="Chronological, oldest first")


class WatchCreate(BaseModel):
    """A route the user wants monitored for price drops."""

    origin: str
    destination: str
    dates_hint: str = "anytime in the next 3 months"
    max_price: float = Field(gt=0, description="Alert when a deal is at or below this price")
    currency: str = Field(default="USD", description="Currency the threshold is expressed in (informational)")
    direct_only: bool = Field(default=False, description="Only consider direct (non-stop) flights")
    cabin_bag: bool = Field(default=False, description="Prices must include a 10kg cabin trolley bag")
    checked_bag: bool = Field(default=False, description="Prices must include a 20kg+ checked bag")


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


class SearchRequest(BaseModel):
    origin: str
    destination: str
    dates_hint: str = Field(
        default="anytime in the next 3 months",
        description="Free-text date preference, e.g. 'first two weeks of October', 'a weekend in November'",
    )
    direct_only: bool = Field(default=False, description="Only consider direct (non-stop) flights")
    cabin_bag: bool = Field(default=False, description="Prices must include a 10kg cabin trolley bag")
    checked_bag: bool = Field(default=False, description="Prices must include a 20kg+ checked bag")
