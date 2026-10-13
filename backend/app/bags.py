"""Price with bags: a deal's fare plus the fees for the bags the search asked for.

Google Flights has no checked-bag filter, and its booking options carry no bag
fees at all for the low-cost carriers (probe 2026-10-13: a Wizz Air TLV-BUD
round trip, 10 sellers, not one baggage line). So the headline "fare" of a
Wizz/Ryanair deal is what the site shows BEFORE bags, and the checkout total is
far higher. This module adds the bags back from our own fee table
(app/data/bag_fees.json: per carrier, per passenger, per direction, as a
range) and reports where each number came from:

- google    carry-on fees Google already put in the fare (we send `bags=N`)
- seller    the seller's own fee from booking options (app/details.py)
- table     our fee table
- typical   no table entry for this airline: a typical low-cost range
- included  the carrier's cheapest fare includes this bag

Prices from the LLM web path (source "web"/legacy) are left as quoted: the
agent was asked to include the requested bags already.

Pure apart from reading the table once; computed on read (storage), so a table
update re-prices every stored deal.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from . import airlines
from .currency import convert
from .models import BagLine, BagPricing, FlightDeal, SearchParams

logger = logging.getLogger(__name__)

TABLE_PATH = Path(__file__).parent / "data" / "bag_fees.json"

# No table entry: a typical low-cost range, per passenger per direction (EUR).
TYPICAL = {"currency": "EUR", "trolley": (10.0, 45.0), "checked": (25.0, 65.0)}

# Sources whose fare excludes checked bags (and carry-on unless Google added it).
_FARE_ONLY_SOURCES = {"google_flights", "google_explore", "aviasales"}
# Sources that send Google's `bags=N` carry-on filter, so the fare has carry-on fees.
_GOOGLE_CARRY_ON = {"google_flights", "google_explore"}

_BASIS_ORDER = ["included", "google", "seller", "table", "typical"]  # strongest first


@lru_cache(maxsize=1)
def table() -> dict:
    """The fee table: {"as_of", "carriers": {code: entry}} plus an alias index."""
    try:
        data = json.loads(TABLE_PATH.read_text())
    except FileNotFoundError:
        data = {}
    except Exception:
        logger.exception("bag fee table unreadable; using typical ranges")
        data = {}
    carriers = data.get("carriers") or {}
    index: dict[str, dict] = {}
    for code, entry in carriers.items():
        entry = {**entry, "code": code}
        index[code.upper()] = entry
        for alias in entry.get("aliases") or []:
            index.setdefault(str(alias).upper(), entry)
    return {"as_of": data.get("as_of", ""), "index": index}


def _codes(deal: FlightDeal) -> list[str]:
    """Every carrier code a deal flies: its flight-number prefixes, its own code,
    then the airlines its name resolves to (brand group codes included)."""
    codes: list[str] = []
    for number in re.split(r"[/,;&+]", deal.flight_numbers or ""):
        prefix = airlines.flight_prefix(number)
        if prefix:
            codes.append(prefix)
    if deal.airline_code:
        codes.append(deal.airline_code.upper())
    _, op_code, matches = airlines.resolve(deal.airline or "", deal.airline_code or "", deal.flight_numbers or "")
    if op_code:
        codes.append(op_code)
    for m in matches:
        if m:
            codes += [m.code, m.group]
    return list(dict.fromkeys(codes))


def carrier_entries(deal: FlightDeal) -> list[dict]:
    """Table entries for the deal's carriers (one per brand), in flight order."""
    index = table()["index"]
    seen, out = set(), []
    for code in _codes(deal):
        entry = index.get(code)
        if entry and entry["code"] not in seen:
            seen.add(entry["code"])
            out.append(entry)
    return out


def _fee(entry: dict | None, kind: str) -> tuple[float, float, str, str, str]:
    """(low, high, currency, basis, carrier name) for one bag, per passenger per direction."""
    if entry:
        fee = entry.get(kind) or {}
        if fee.get("included"):
            return 0.0, 0.0, entry.get("currency", "EUR"), "included", entry.get("name", "")
        lo, hi = fee.get("min"), fee.get("max")
        if lo is not None or hi is not None:
            lo = float(lo if lo is not None else hi)
            hi = float(hi if hi is not None else lo)
            return lo, hi, entry.get("currency", "EUR"), "table", entry.get("name", "")
    lo, hi = TYPICAL[kind]
    return lo, hi, TYPICAL["currency"], "typical", (entry or {}).get("name", "")


def _worst(fees: list[tuple[float, float, str, str, str]], currency: str) -> tuple[float, float, str, str]:
    """The dearest of several carriers' fees (a multi-airline trip pays each
    carrier's own fee on its leg; without per-leg data we take the upper one),
    converted into `currency`. Returns (low, high, basis, carrier)."""
    best = None
    for lo, hi, ccy, basis, name in fees:
        lo_c, hi_c = convert(lo, ccy, currency), convert(hi, ccy, currency)
        if lo_c is None or hi_c is None:
            lo_c, hi_c = convert(lo, ccy, "USD") or lo, convert(hi, ccy, "USD") or hi
            basis = "typical"
        cand = (lo_c, hi_c, basis, name)
        if best is None or cand[1] > best[1]:
            best = cand
    return best


def _directions(deal: FlightDeal) -> int:
    return 2 if deal.return_date else 1


def price_with_bags(deal: FlightDeal, params: SearchParams | None) -> BagPricing:
    """The deal's price with the bags `params` asks for, in the deal currency."""
    price = float(deal.price)
    plain = BagPricing(basis="none", fare=price, total=price, total_min=price, total_max=price,
                       currency=deal.currency, as_of=table()["as_of"])
    if params is None:
        return plain
    seats = max(params.passengers.adults + params.passengers.children, 1)
    trolleys = seats if params.cabin_bag else 0
    checked = params.checked_bags or (seats if params.checked_bag else 0)
    if not trolleys and not checked:
        return plain
    if deal.source not in _FARE_ONLY_SOURCES:
        return plain.model_copy(update={"basis": "as_quoted"})

    dirs = _directions(deal)
    entries = carrier_entries(deal) or [None]
    lines: list[BagLine] = []
    for kind, count in (("trolley", trolleys), ("checked", checked)):
        if not count:
            continue
        if kind == "trolley" and deal.source in _GOOGLE_CARRY_ON:
            lines.append(BagLine(kind=kind, count=count, directions=dirs, low=0, high=0, basis="google",
                                 carrier=deal.airline))
            continue
        lo, hi, basis, name = _worst([_fee(e, kind) for e in entries], deal.currency)
        n = count * dirs
        lines.append(BagLine(kind=kind, count=count, directions=dirs, low=round(lo * n, 2), high=round(hi * n, 2),
                             basis=basis, carrier=name or deal.airline))
    return _priced(price, deal.currency, lines)


def _priced(price: float, currency: str, lines: list[BagLine]) -> BagPricing:
    low = sum(l.low for l in lines)
    high = sum(l.high for l in lines)
    weakest = max((l.basis for l in lines), key=_BASIS_ORDER.index, default="included")
    return BagPricing(
        basis="included" if weakest == "google" else weakest,
        fare=price,
        total=round(price + (low + high) / 2, 2),
        total_min=round(price + low, 2),
        total_max=round(price + high, 2),
        currency=currency,
        lines=lines,
        added_at_checkout=any(l.high > 0 and l.basis != "google" for l in lines),
        as_of=table()["as_of"],
    )


def with_seller_fees(pricing: BagPricing, fees: dict[str, float | None]) -> BagPricing:
    """Replace table lines with a seller's exact per-passenger-per-direction
    fees (app/details.py: {"trolley": 25.0, "checked": None}); None keeps the table line."""
    lines = []
    for line in pricing.lines:
        fee = fees.get(line.kind)
        if fee is None or line.basis in ("google", "included"):
            lines.append(line)
            continue
        total = round(fee * line.count * line.directions, 2)
        lines.append(line.model_copy(update={"low": total, "high": total, "basis": "seller"}))
    return _priced(pricing.fare, pricing.currency, lines)


def row_pricing(row: dict) -> BagPricing:
    """price_with_bags for a light sighting row (storage.watch_sightings,
    alerts.deal_rows): price/currency/source/search_params plus the airline
    fields and return_date when the row has them."""
    params = row.get("search_params")
    if params is not None and not isinstance(params, SearchParams):
        try:
            params = SearchParams.model_validate_json(params) if params else None
        except Exception:
            params = None
    deal = FlightDeal(origin="", destination="", airline=row.get("airline") or "",
                      airline_code=row.get("airline_code") or "", flight_numbers=row.get("flight_numbers") or "",
                      price=float(row["price"]), currency=row.get("currency") or "USD", depart_date="",
                      return_date=row.get("return_date") or "", source=row.get("source") or "")
    return price_with_bags(deal, params)
