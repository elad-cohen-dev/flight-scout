"""A price you can check: re-run a live fare, and log what the seller really charged.

    recheck(deal)        -> the same Google Flights query again, fresh (1 credit)
    add_check(create)    -> one "was it the same price?" answer after a Book click
    summary()            -> per airline: checks, % that matched, median difference

Recheck re-sends the deal's stored SerpApi request (FlightDeal.serp_params) with
our response cache AND SerpApi's own 1h cache bypassed, so it always spends one
search of the monthly budget (the usual serpapi.fetch accounting). The fare is
matched by its outbound flight numbers and departure time; when Google lists it
again the new sighting is stored as a deal row under the same search label (so
History and the watches see it), else the fare is reported gone. Only live
Google Flights deals can be rechecked: the other sources have nothing exact to
re-run.

Price checks are the family's own ground truth: after clicking a Book link, the
UI asks whether the seller's total matched what we showed (the price with bags
when bags were asked for). The summary is what the Trends tab prints, e.g.
"Price checks: 7 of 9 matched; Wizz Air off by ~€15".
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from . import storage
from .currency import convert, normalize
from .models import FlightDeal, SearchParams, StoredDeal
from .providers import serpapi
from .providers.base import BudgetExhausted, FareQuery, ProviderError

logger = logging.getLogger(__name__)

SAME_TOLERANCE = 0.01  # |difference| / shown under this counts as the same price


class RecheckError(Exception):
    """A recheck that can't go ahead; `status` is the HTTP status for the API."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class RecheckResult(BaseModel):
    status: str = Field(description="same | cheaper | pricier | gone")
    old: float = Field(description="The deal's fare, in `currency`")
    new: float | None = Field(default=None, description="Google's fare now; None = no longer listed")
    delta: float | None = Field(default=None, description="new - old")
    currency: str
    old_seen_at: str = Field(default="", description="When the old fare was seen (seen_at, else found_at)")
    deal: StoredDeal | None = Field(default=None, description="The stored new sighting")
    spent: int = 1
    left: int = 0


# --- recheck -------------------------------------------------------------------

_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _lock_for(deal_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks[deal_id]


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s+", "", n).upper() for n in re.split(r"[/,;&+]", text or "") if n.strip()}


def _query(deal: StoredDeal, params: dict) -> FareQuery:
    return FareQuery(
        origin=str(params.get("departure_id") or deal.origin_airport or deal.origin),
        destination=str(params.get("arrival_id") or deal.destination_airport or deal.destination),
        depart_date=str(params.get("outbound_date") or deal.depart_date),
        return_date=str(params.get("return_date") or ""),
        params=deal.search_params or SearchParams(currency=str(params.get("currency") or deal.currency)),
    )


def match(old: FlightDeal, candidates: list[FlightDeal]) -> FlightDeal | None:
    """The same itinerary in a fresh answer: its outbound flight numbers are
    all among the old deal's (a round trip's stored numbers also carry the
    return flights), same departure date and time; cheapest on a tie. Without
    flight numbers, airline + departure time decide."""
    mine = _numbers(old.flight_numbers)
    found = []
    for c in candidates:
        theirs = _numbers(c.flight_numbers)
        if mine and theirs:
            same = theirs <= mine
        else:
            same = bool(old.depart_time) and c.airline.lower() == old.airline.lower()
        if same and c.depart_date == old.depart_date and (not old.depart_time or c.depart_time == old.depart_time):
            found.append(c)
    return min(found, key=lambda c: c.price, default=None)


def _status(delta: float | None, old: float) -> str:
    if delta is None:
        return "gone"
    if abs(delta) <= max(1.0, old * 0.005):
        return "same"
    return "cheaper" if delta < 0 else "pricier"


def recheck(deal: StoredDeal) -> RecheckResult:
    """One fresh live search for the deal's exact dates (see the module docstring)."""
    if deal.source != serpapi.SOURCE or not deal.serp_params:
        raise RecheckError(400, "Recheck works on live Google Flights fares only.")
    try:
        params = json.loads(deal.serp_params)
    except ValueError:
        params = None
    if not isinstance(params, dict) or not params.get("departure_id"):
        raise RecheckError(400, "This fare has no stored Google Flights query to re-run.")
    if not serpapi.configured():
        raise RecheckError(503, "Live Google Flights search isn't configured on this server.")
    params = {k: v for k, v in params.items() if k not in ("departure_token", "booking_token")}

    with _lock_for(deal.id):
        if serpapi.remaining() < 1:
            raise RecheckError(409, f"This month's live search budget ({serpapi.monthly_budget()}) is used up.")
        try:
            data, _ = serpapi.fetch(params, fresh=True)
        except BudgetExhausted:
            raise RecheckError(409, f"This month's live search budget ({serpapi.monthly_budget()}) is used up.") \
                from None
        except ProviderError as exc:
            logger.warning("serpapi recheck failed: %s", exc)
            raise RecheckError(502, "Google Flights didn't answer just now — try again in a minute.") from None

    candidates, _ = serpapi.map_response(data, _query(deal, params))
    found = match(deal, candidates)
    out = RecheckResult(status="gone", old=deal.price, currency=deal.currency,
                        old_seen_at=deal.seen_at or deal.found_at.isoformat(), left=serpapi.remaining())
    if found is None:
        return out
    # Keep what only the old row knew (the return flight learned by details);
    # Google prices the cheapest return with this outbound, as it did before.
    fresh = found.model_copy(update={
        "flight_numbers": deal.flight_numbers or found.flight_numbers,
        "return_depart_time": deal.return_depart_time or found.return_depart_time,
        "serp_params": deal.serp_params,
        "quality_flags": [],
    })
    if found.currency != deal.currency:  # can't happen with the same request; be safe
        fresh_price = convert(found.price, found.currency, deal.currency)
    else:
        fresh_price = found.price
    [stored] = storage.save_deals(deal.search_query, [fresh], deal.search_params)
    out.new = round(fresh_price, 2) if fresh_price is not None else None
    out.delta = round(out.new - deal.price, 2) if out.new is not None else None
    out.status = _status(out.delta, deal.price) if out.new is not None else "gone"
    out.deal = stored
    return out


# --- price-check log -------------------------------------------------------------

class PriceCheckCreate(BaseModel):
    deal_id: int
    seller: str = Field(default="", max_length=120, description="The Book link's label, e.g. 'Wizz Air'")
    shown_total: float = Field(gt=0, description="What Flight Scout showed (with bags when asked)")
    actual_total: float | None = Field(default=None, gt=0, description="What the seller's checkout said; None = same")
    same: bool
    currency: str = "USD"

    @field_validator("currency")
    @classmethod
    def _iso(cls, v: str) -> str:
        return normalize(v)


class PriceCheck(PriceCheckCreate):
    id: int
    airline: str = ""
    bag_basis: str = ""
    created_at: str


class AirlineAccuracy(BaseModel):
    airline: str
    checks: int
    same: int
    same_pct: int
    median_diff: float | None = Field(default=None, description="Median (actual - shown) of checks with a total")
    currency: str = ""
    bags_estimated: int = Field(default=0, description="Checks whose shown price had estimated bag fees")


class PriceCheckSummary(BaseModel):
    checks: int = 0
    same: int = 0
    airlines: list[AirlineAccuracy] = Field(default_factory=list)


def add_check(body: PriceCheckCreate, deal: StoredDeal) -> PriceCheck:
    """Store an answer. "Same" with no total records the shown total; a typed
    total within 1% of the shown one counts as the same."""
    actual = body.actual_total
    same = body.same
    if same and actual is None:
        actual = body.shown_total
    elif actual is not None:
        same = abs(actual - body.shown_total) <= body.shown_total * SAME_TOLERANCE
    row = {
        "deal_id": deal.id, "seller": body.seller.strip(), "airline": deal.airline,
        "bag_basis": deal.bags.basis if deal.bags else "", "shown_total": round(body.shown_total, 2),
        "actual_total": round(actual, 2) if actual is not None else None, "same": same,
        "currency": body.currency, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return PriceCheck(**storage.add_price_check(row))


def summarize(rows: list[dict]) -> PriceCheckSummary:
    """Per airline (most checked first): how often the price matched and the
    median difference, converted into that airline's most used currency."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r.get("airline") or r.get("seller") or "Unknown"].append(r)
    airlines = []
    for name, items in groups.items():
        ccy = Counter(normalize(r["currency"]) for r in items).most_common(1)[0][0]
        diffs = []
        for r in items:
            if r.get("actual_total") is None:
                continue
            d = convert(r["actual_total"] - r["shown_total"], r["currency"], ccy)
            if d is not None:
                diffs.append(d)
        same = sum(1 for r in items if r["same"])
        airlines.append(AirlineAccuracy(
            airline=name, checks=len(items), same=same, same_pct=round(100 * same / len(items)),
            median_diff=round(statistics.median(diffs), 2) if diffs else None, currency=ccy,
            bags_estimated=sum(1 for r in items if r.get("bag_basis") in ("table", "typical")),
        ))
    airlines.sort(key=lambda a: (-a.checks, a.airline))
    return PriceCheckSummary(checks=len(rows), same=sum(1 for r in rows if r["same"]), airlines=airlines)


def summary() -> PriceCheckSummary:
    return summarize(storage.list_price_checks())
