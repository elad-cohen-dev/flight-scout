"""Build pre-filled booking links for a deal.

Owned by the booking workstream. Contract (used by storage when returning
deals, so legacy rows get links too):

    build_links(deal, params) -> list[BookingLink]

Must be pure (no network), never raise, and always return at least one
useful link when the deal has a route.
"""

from __future__ import annotations

from .models import BookingLink, FlightDeal, SearchParams


def build_links(deal: FlightDeal, params: SearchParams | None) -> list[BookingLink]:
    links: list[BookingLink] = []
    if deal.booking_url:
        links.append(BookingLink(label="Book", url=deal.booking_url, kind="other"))
    return links
