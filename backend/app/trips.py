"""Trips (3.0): a family trip plan resolved into what the UI and the bot show.

A trip links searches (by their stored label), watches and shortlisted deals
(storage.trip_items). Nothing here is stored: on every read each link is
resolved to its current state — a search to its latest run's deals, a watch to
its stats, a deal to its row with the price with bags — and the trip's best
price is the cheapest of those, with bags, in the trip's currency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from . import alerts, storage
from .currency import convert
from .models import (
    StoredDeal, Trip, TripBest, TripDealItem, TripDetail, TripItem, TripSearchItem, TripWatchItem, Watch,
)

logger = logging.getLogger(__name__)

# A search item shows its latest run's cheapest deals, not the whole run.
SEARCH_DEALS = 8


def deal_total(deal: StoredDeal) -> tuple[float, str]:
    """A deal's price with the requested bags and its currency: compare_total
    (in the run's currency) when the backend could convert it, else the raw
    price with bags."""
    if deal.compare_total is not None:
        return deal.compare_total, deal.compare_currency
    if deal.compare_price is not None:
        return deal.compare_price, deal.compare_currency
    return (deal.bags.total if deal.bags else deal.price), deal.currency


def _sort_key(deal: StoredDeal) -> float:
    return deal_total(deal)[0]


@dataclass
class _Resolved:
    """Everything a set of trip items points at, fetched once."""

    deals: dict[int, StoredDeal] = field(default_factory=dict)
    watches: dict[int, Watch] = field(default_factory=dict)
    searches: dict[str, tuple[list[StoredDeal], int]] = field(default_factory=dict)


def _resolve(items: list[TripItem], booked_ids: list[int] | None = None,
             now: datetime | None = None) -> _Resolved:
    out = _Resolved()
    deal_ids = [i.ref_id for i in items if i.kind == "deal" and i.ref_id is not None] + (booked_ids or [])
    if deal_ids:
        out.deals = storage.get_deals(deal_ids)
    if any(i.kind == "watch" for i in items):
        enriched = alerts.enrich_watches(storage.list_watches(), storage.watch_sightings(), storage.list_alerts(),
                                         now, insights=storage.watch_price_insights())
        out.watches = {w.id: w for w in enriched}
    for label in {i.search_query for i in items if i.kind == "search"}:
        out.searches[label] = storage.search_runs(label)
    return out


def _best(trip: Trip, items: list[TripItem], r: _Resolved) -> TripBest | None:
    """The cheapest price with bags across the trip's items, in its currency.
    Prices that can't be converted are left out."""
    candidates: list[TripBest] = []

    def consider(amount: float | None, currency: str, kind: str, label: str, deal_id: int | None = None) -> None:
        if amount is None:
            return
        price = convert(amount, currency, trip.currency)
        if price is not None:
            candidates.append(TripBest(price=round(price, 2), currency=trip.currency, kind=kind,
                                       label=label, deal_id=deal_id))

    def deal_label(d: StoredDeal) -> str:
        return f"{d.airline or 'Airline n/a'} · {storage._route_of(d.search_query)}"

    for item in items:
        if item.kind == "deal" and (d := r.deals.get(item.ref_id)):
            consider(*deal_total(d), "deal", deal_label(d), d.id)
        elif item.kind == "search":
            for d in r.searches.get(item.search_query, ([], 0))[0]:
                consider(*deal_total(d), "search", deal_label(d), d.id)
        elif item.kind == "watch" and (w := r.watches.get(item.ref_id)):
            # Watch prices are already the price with bags, in the watch currency (watcher.cheapest).
            last = w.stats.last_best if w.stats and w.stats.last_best is not None else w.last_best_price
            consider(last, w.currency, "watch", f"Watch #{w.id} · {w.origin} -> {w.destination}")
    return min(candidates, key=lambda b: b.price) if candidates else None


def with_best(trips: list[Trip], now: datetime | None = None) -> list[Trip]:
    """Copies of `trips` carrying their best price (one resolve for all of them)."""
    if not trips:
        return []
    all_items = storage.list_trip_items()
    resolved = _resolve(all_items, now=now)
    by_trip: dict[int, list[TripItem]] = {}
    for item in all_items:
        by_trip.setdefault(item.trip_id, []).append(item)
    out = []
    for trip in trips:
        try:
            best = _best(trip, by_trip.get(trip.id, []), resolved)
        except Exception:
            logger.exception("trip #%s: best price failed", trip.id)
            best = None
        out.append(trip.model_copy(update={"best": best}))
    return out


def detail(trip: Trip, now: datetime | None = None) -> TripDetail:
    """The trip with every link resolved: searches (latest run's cheapest
    deals), watches (with stats), the shortlist (pinned first, then cheapest)
    and the booked deal."""
    items = storage.list_trip_items(trip.id)
    booked_id = trip.booked.deal_id if trip.booked else None
    r = _resolve(items, [booked_id] if booked_id is not None else None, now)

    searches = []
    for item in (i for i in items if i.kind == "search"):
        deals, runs = r.searches.get(item.search_query, ([], 0))
        searches.append(TripSearchItem(
            item=item,
            route=storage._route_of(item.search_query),
            runs=runs,
            latest_run_at=deals[0].found_at if deals else None,
            deals=sorted(deals, key=_sort_key)[:SEARCH_DEALS],
        ))
    watches = [TripWatchItem(item=i, watch=r.watches.get(i.ref_id)) for i in items if i.kind == "watch"]
    shortlist = [TripDealItem(item=i, deal=r.deals.get(i.ref_id)) for i in items if i.kind == "deal"]

    def in_trip_currency(d: StoredDeal | None) -> float:
        # Shortlisted deals come from different runs, so different currencies.
        if d is None:
            return float("inf")
        converted = convert(*deal_total(d), trip.currency)
        return converted if converted is not None else float("inf")

    shortlist.sort(key=lambda x: (not x.item.pinned, in_trip_currency(x.deal)))
    return TripDetail(
        **trip.model_dump(exclude={"best"}),
        best=_best(trip, items, r),
        searches=searches,
        watches=watches,
        deals=shortlist,
        booked_deal=r.deals.get(booked_id) if booked_id is not None else None,
    )
