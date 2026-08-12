"""Pydantic models shared by the agent, storage, and API layers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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


class FlightDealList(BaseModel):
    """Structured-output wrapper: the list of deals extracted from research notes."""

    deals: list[FlightDeal] = Field(description="All distinct flight deals found, cheapest first")


class StoredDeal(FlightDeal):
    """A deal as returned from the database."""

    id: int
    search_query: str
    found_at: datetime


class WatchCreate(BaseModel):
    """A route the user wants monitored for price drops."""

    origin: str
    destination: str
    dates_hint: str = "anytime in the next 3 months"
    max_price: float = Field(gt=0, description="Alert when a deal is at or below this price")
    currency: str = Field(default="USD", description="Currency the threshold is expressed in (informational)")


class Watch(WatchCreate):
    id: int
    active: bool = True
    created_at: datetime
    last_run_at: datetime | None = None
    last_best_price: float | None = None


class SearchRequest(BaseModel):
    origin: str
    destination: str
    dates_hint: str = Field(
        default="anytime in the next 3 months",
        description="Free-text date preference, e.g. 'first two weeks of October', 'a weekend in November'",
    )
