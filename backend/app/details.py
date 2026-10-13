"""Deal details on demand: a Google Flights deal's return flight, every seller
with its exact price and bag fees, and "Book with <seller>" redirects.

    get_details(deal)   -> cached details, or what a fetch would cost (free)
    fetch_details(deal) -> spends live searches (SerpApi), then caches

A search stores each itinerary's SerpApi tokens on its deal row
(models.FlightDeal.departure_token / booking_token / serp_params). A fetch
spends at most two searches of the monthly budget:

1. Round trip without a booking token: departure_token -> the return flights;
   the one whose total matches the deal's is the return leg, and its
   booking_token is kept on the row (so a later re-fetch costs one search).
2. booking_token -> booking_options (each seller, its price, baggage_prices and
   Google's redirect request) + selected_flights (both legs' exact times).

One-way deals carry a booking_token from the search, so they cost one search.
Calls already in the 1h response cache are free and not counted.

Tokens go stale (Google re-prices; SerpApi doesn't document a lifetime). An
empty answer or a token error is reported as "expired" — search again — and
that verdict is cached like a result, so retrying never re-spends.

Booking: Google's booking_request is a POST form to google.com/travel/clk/...
that redirects to the seller. A browser can only make that POST itself, so
GET /api/deals/{id}/book/{option} serves a tiny page that auto-submits the
form (escaped hidden inputs from post_data; https google.com targets only).
Browser navigation can't carry the app's Bearer header, so book links are
HMAC-signed with APP_PASSWORD and expire with the cached details.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

from pydantic import BaseModel, Field

from . import storage
from .models import StoredDeal
from .providers import base, serpapi
from .providers.base import BudgetExhausted, ProviderError

logger = logging.getLogger(__name__)

DETAILS_TTL_SECONDS = 6 * 3600  # sellers re-price; past this a fetch is needed again
CACHE_PROVIDER = "serpapi-details"
EXPIRED_MESSAGE = ("Google's reference for this fare has expired (fares are re-priced often). "
                   "Search this route again for fresh prices and booking options.")
_TOKEN_ERROR = re.compile(r"token|expired", re.IGNORECASE)
_API_KEY_ERROR = re.compile(r"api[ _]key", re.IGNORECASE)

Status = Literal["ready", "needs_fetch", "expired", "unavailable"]


class DetailsError(Exception):
    """A fetch that can't go ahead; `status` is the HTTP status for the API."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --- response models -----------------------------------------------------------

class Leg(BaseModel):
    origin: str = ""
    destination: str = ""
    depart_date: str = ""
    depart_time: str = ""
    arrive_date: str = ""
    arrive_time: str = ""
    airline: str = ""
    flight_numbers: str = ""
    duration: str = ""
    stops: int = 0
    layovers: list[str] = Field(default_factory=list)


class BagFee(BaseModel):
    text: str = Field(default="", description="Google's wording, e.g. '1st checked bag: 35'; '' = not stated")
    fee: float | None = Field(default=None, description="Exact fee per traveller (0 = free); None = unknown or a range")
    max_fee: float | None = Field(default=None, description="Upper bound per traveller: the fee, or a range's top; None = unknown")


class SellerLink(BaseModel):
    part: Literal["together", "departing", "returning"]
    label: str
    url: str = Field(default="", description="Signed /api/deals/{id}/book/{option} path; '' when Google gave no booking link")


class Seller(BaseModel):
    index: int
    book_with: str
    airline: bool = Field(description="Sold by the airline itself (book direct)")
    price: float | None = Field(description="Total for the party in `currency`")
    option_title: str = ""
    extensions: list[str] = Field(default_factory=list)
    carry_on: BagFee = Field(default_factory=BagFee)
    checked: BagFee = Field(default_factory=BagFee)
    bag_notes: list[str] = Field(default_factory=list, description="All of Google's baggage lines for this seller")
    price_incl_bags: float | None = Field(default=None, description="price + the requested bags' fees, when all are known")
    price_incl_bags_max: float | None = Field(
        default=None, description="Upper bound of price + requested bags (a range's top); None = a fee is unknown")
    bags_verified: bool = Field(default=False, description="Every requested bag's fee is known exactly")
    separate_tickets: bool = False
    phone: str = ""
    links: list[SellerLink] = Field(default_factory=list)


class DealDetails(BaseModel):
    deal_id: int
    status: Status
    message: str = ""
    cost: int = Field(default=0, description="Live searches a fetch would spend now (needs_fetch)")
    left: int = Field(default=0, description="Live searches left this month")
    budget: int = 0
    spent: int = Field(default=0, description="Live searches this request spent")
    fetched_at: str = ""
    currency: str = ""
    outbound: Leg | None = None
    return_leg: Leg | None = None
    sellers: list[Seller] = Field(default_factory=list)
    bags_requested: list[Literal["carry_on", "checked"]] = Field(default_factory=list)
    verified_incl_bags: bool = Field(default=False, description="A seller's price incl. every requested bag is known")
    price_incl_bags: float | None = Field(default=None, description="Cheapest such price")


# --- bag fees ------------------------------------------------------------------

_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)(?:\s*[-–]\s*(\d[\d,]*(?:\.\d+)?))?")
_CARRY_ON = re.compile(r"carry[- ]?on|cabin bag|hand (bag|luggage)", re.IGNORECASE)
_CHECKED = re.compile(r"checked", re.IGNORECASE)
_NOT_INCLUDED = re.compile(r"\bno\b|not (included|allowed)|isn'?t included", re.IGNORECASE)


def parse_bag(lines: list[str], kind: Literal["carry_on", "checked"]) -> BagFee:
    """The first baggage line about `kind`: '1 free carry-on' -> fee 0,
    '1st checked bag: 35' -> 35, '1st checked bag: 99-187' -> a range (fee None),
    'No carry-on included' -> stated but no fee."""
    pattern = _CARRY_ON if kind == "carry_on" else _CHECKED
    for line in lines:
        if not pattern.search(line):
            continue
        if re.search(r"\bfree\b|\bincluded\b", line, re.IGNORECASE) and not _NOT_INCLUDED.search(line):
            return BagFee(text=line, fee=0.0, max_fee=0.0)
        if _NOT_INCLUDED.search(line):
            return BagFee(text=line)
        tail = line.split(":", 1)[1] if ":" in line else ""
        m = _AMOUNT.search(tail)
        if m:
            low = float(m.group(1).replace(",", ""))
            high = float(m.group(2).replace(",", "")) if m.group(2) else low
            return BagFee(text=line, fee=None if m.group(2) else low, max_fee=high)
        return BagFee(text=line)
    return BagFee()


def _combine(fees: list[BagFee]) -> BagFee:
    """Separate tickets: each ticket's bag fee adds up; unknown if any is."""
    if len(fees) == 1:
        return fees[0]
    text = " / ".join(f.text for f in fees if f.text)
    top = None if any(f.max_fee is None for f in fees) else sum(f.max_fee for f in fees)
    if any(f.fee is None for f in fees):
        return BagFee(text=text, max_fee=top)
    return BagFee(text=text, fee=sum(f.fee for f in fees), max_fee=top)


def _requested_bags(deal: StoredDeal) -> list[str]:
    p = deal.search_params
    if p is None:
        return []
    return [k for k, on in (("carry_on", p.cabin_bag), ("checked", p.checked_bag)) if on]


def _bag_counts(deal: StoredDeal, parts: int = 1) -> dict[str, int]:
    """Bags bought per kind for the whole trip: per-direction count x directions.
    Google's baggage lines ('1st checked bag: 35') are taken as per passenger,
    per direction — the airlines' own convention; the price-check log tells us
    if a seller means otherwise. Separate tickets (one part per direction)
    already sum their parts' fees (_combine), so they count one direction.
    Carry-on is 0 when the request already sent Google's bags filter: the
    seller's price then includes it."""
    p = deal.search_params
    if p is None:
        return {}
    dirs = 2 if deal.return_date and parts < 2 else 1
    seats = base.paying(p)
    carry = 0 if (not p.cabin_bag or _params(deal).get("bags")) else seats
    checked = p.checked_bags or (seats if p.checked_bag else 0)
    return {"carry_on": carry * dirs, "checked": checked * dirs}


# --- cache + cost --------------------------------------------------------------

def _ref(deal: StoredDeal) -> str:
    """The deal's stable details reference: the outbound's departure token on
    round trips (the booking token is only learned later), else the booking token."""
    return deal.departure_token or deal.booking_token


def available(deal: StoredDeal) -> bool:
    return deal.source == serpapi.SOURCE and bool(deal.serp_params) and bool(_ref(deal))


def _params(deal: StoredDeal) -> dict:
    try:
        params = json.loads(deal.serp_params)
    except ValueError:
        return {}
    return params if isinstance(params, dict) else {}


def _cached_details(deal: StoredDeal) -> dict | None:
    try:
        return storage.cache_get(base.cache_key(CACHE_PROVIDER, {"ref": _ref(deal)}))
    except Exception:
        logger.exception("details cache read failed")
        return None


def _store_details(deal: StoredDeal, payload: dict) -> None:
    base.store(CACHE_PROVIDER, {"ref": _ref(deal)}, payload, DETAILS_TTL_SECONDS)


def _response_cached(params: dict) -> bool:
    """Whether serpapi.fetch would answer `params` from our response cache (free)."""
    try:
        return storage.cache_get(base.cache_key(serpapi.PROVIDER, params)) is not None
    except Exception:
        return False


def cost(deal: StoredDeal) -> int:
    """Live searches a fetch would spend right now (cache hits are free)."""
    params = _params(deal)
    without_return = {k: v for k, v in params.items() if k != "departure_token"}
    if deal.booking_token:
        return 0 if _response_cached({**without_return, "booking_token": deal.booking_token}) else 1
    return (0 if _response_cached({**params, "departure_token": deal.departure_token}) else 1) + 1


# --- book links ----------------------------------------------------------------

def _secret() -> str:
    return os.environ.get("APP_PASSWORD", "")


def _signature(deal_id: int, option: int, part: str, exp: int) -> str:
    msg = f"{deal_id}:{option}:{part}:{exp}".encode()
    return hmac.new(_secret().encode(), msg, hashlib.sha256).hexdigest()[:32]


def book_path(deal_id: int, option: int, part: str, now: float | None = None) -> str:
    exp = int((now or time.time()) + DETAILS_TTL_SECONDS)
    query = {"part": part, "exp": exp}
    if _secret():
        query["sig"] = _signature(deal_id, option, part, exp)
    return f"/api/deals/{deal_id}/book/{option}?{urlencode(query)}"


def book_link_valid(deal_id: int, option: int, part: str, exp: int | None, sig: str | None,
                    now: float | None = None) -> bool:
    """A signed, unexpired book link. Always valid while APP_PASSWORD is unset
    (the API is open then too)."""
    if not _secret():
        return True
    if exp is None or not sig or exp < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _signature(deal_id, option, part, exp))


def safe_target(url: str) -> bool:
    """Only Google's own https redirect endpoints may be form targets."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "google.com" or host.endswith(".google.com"))


def booking_request(deal: StoredDeal, option: int, part: str) -> tuple[str, str, str] | None:
    """(seller, url, post_data) of a cached seller's ticket; None once the
    details expired from the cache or the option doesn't exist."""
    payload = _cached_details(deal) if available(deal) else None
    options = (payload or {}).get("options") or []
    if not 0 <= option < len(options):
        return None
    for p in options[option].get("parts") or []:
        if p.get("part") == part and p.get("url"):
            return p.get("book_with") or "the seller", p["url"], p.get("post_data") or ""
    return None


_PAGE_STYLE = ("body{font:16px/1.5 system-ui,-apple-system,sans-serif;margin:0;min-height:100vh;display:grid;"
               "place-items:center;background:#f4f6fa;color:#0f172a;padding:16px;box-sizing:border-box}"
               "main{max-width:26rem;text-align:center}button,a.btn{font:inherit;font-weight:600;border:0;"
               "border-radius:8px;padding:.6rem 1.1rem;background:#2563eb;color:#fff;cursor:pointer;"
               "text-decoration:none;display:inline-block}p{color:#5b6b82}"
               "@media (prefers-color-scheme:dark){body{background:#0b1120;color:#e6ebf3}p{color:#93a1b7}"
               "button,a.btn{background:#4f8bff}}")


def _page(title: str, body: str, nonce: str = "", script: str = "") -> str:
    tag = f'<script nonce="{nonce}">{script}</script>' if script else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="referrer" content="no-referrer"><title>{html.escape(title)}</title>'
            f'<style>{_PAGE_STYLE}</style></head><body><main>{body}</main>{tag}</body></html>')


def redirect_page(seller: str, url: str, post_data: str, nonce: str) -> str:
    """Auto-submitting POST form to Google's booking redirect. Every value is
    HTML-escaped; post_data ('u=...&...') becomes one hidden input per field."""
    fields = parse_qsl(post_data, keep_blank_values=True)
    inputs = "".join(f'<input type="hidden" name="{html.escape(k, quote=True)}" '
                     f'value="{html.escape(v, quote=True)}">' for k, v in fields)
    name = html.escape(seller)
    body = (f'<form id="book" method="post" action="{html.escape(url, quote=True)}">{inputs}'
            f'<p>Taking you to {name} via Google Flights…</p>'
            f'<button type="submit">Continue to {name}</button></form>')
    return _page(f"Opening {seller}…", body, nonce, 'document.getElementById("book").submit();')


def message_page(title: str, text: str) -> str:
    return _page(title, f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>")


def new_nonce() -> str:
    return secrets.token_urlsafe(16)


# --- building the response -----------------------------------------------------

def _seller(deal: StoredDeal, index: int, option: dict, bags: list[str]) -> Seller:
    parts = option.get("parts") or []
    prices = [p.get("price") for p in parts]
    price = float(sum(prices)) if prices and all(isinstance(x, (int, float)) for x in prices) else None
    lines = [[str(b) for b in p.get("baggage_prices") or []] for p in parts]
    carry = _combine([parse_bag(l, "carry_on") for l in lines])
    checked = _combine([parse_bag(l, "checked") for l in lines])
    counts = _bag_counts(deal, len(parts))
    fees = {"carry_on": carry.fee, "checked": checked.fee}
    needed = [b for b in bags if counts.get(b)]
    known = price is not None and all(fees[b] is not None for b in needed)
    incl = round(price + sum(fees[b] * counts[b] for b in needed), 2) if known else None
    tops = {"carry_on": carry.max_fee, "checked": checked.max_fee}
    bounded = price is not None and all(tops[b] is not None for b in needed)
    top = round(price + sum(tops[b] * counts[b] for b in needed), 2) if bounded else None
    names = [p.get("book_with") or "" for p in parts]
    return Seller(
        index=index,
        book_with=" + ".join(n for n in dict.fromkeys(names) if n) or "Unknown seller",
        airline=bool(parts) and all(p.get("airline") for p in parts),
        price=price,
        option_title=" / ".join(t for t in dict.fromkeys(p.get("option_title") or "" for p in parts) if t),
        extensions=list(dict.fromkeys(e for p in parts for e in p.get("extensions") or [])),
        carry_on=carry,
        checked=checked,
        bag_notes=list(dict.fromkeys(b for l in lines for b in l)),
        price_incl_bags=incl,
        price_incl_bags_max=top,
        bags_verified=bool(bags) and known,
        separate_tickets=bool(option.get("separate_tickets")),
        phone=next((p.get("phone") for p in parts if p.get("phone")), ""),
        links=[SellerLink(part=p["part"], label=p.get("book_with") or "seller",
                          url=book_path(deal.id, index, p["part"]) if p.get("url") else "")
               for p in parts if p.get("part") in ("together", "departing", "returning")],
    )


def seller_rank(s: Seller) -> tuple:
    """Cheapest first by the price incl. the requested bags, a range counted at
    its top (it could be that high); exact totals beat inexact ones on ties,
    then airlines (book direct). A seller whose bag fee is unknown goes last."""
    total = s.price_incl_bags if s.price_incl_bags is not None else s.price_incl_bags_max
    exact = s.price_incl_bags is not None
    return (total is None, total or 0, not exact, not s.airline, s.price is None, s.price or 0)


def _usage(out: DealDetails) -> DealDetails:
    out.left, out.budget = serpapi.remaining(), serpapi.monthly_budget()
    return out


def _from_payload(deal: StoredDeal, payload: dict, spent: int = 0) -> DealDetails:
    bags = _requested_bags(deal)
    out = DealDetails(
        deal_id=deal.id,
        status="expired" if payload.get("expired") else "ready",
        message=payload.get("message") or "",
        spent=spent,
        fetched_at=payload.get("fetched_at") or "",
        currency=payload.get("currency") or deal.currency,
        outbound=Leg(**payload["outbound"]) if payload.get("outbound") else None,
        return_leg=Leg(**payload["return_leg"]) if payload.get("return_leg") else None,
        bags_requested=bags,
    )
    out.sellers = [_seller(deal, i, o, bags) for i, o in enumerate(payload.get("options") or [])]
    out.sellers.sort(key=seller_rank)
    verified = [s.price_incl_bags for s in out.sellers if s.bags_verified]
    if verified:
        out.verified_incl_bags, out.price_incl_bags = True, min(verified)
    return _usage(out)


def get_details(deal: StoredDeal) -> DealDetails:
    """Cached details, or what a fetch would cost. Never spends a search."""
    if not available(deal):
        return _usage(DealDetails(deal_id=deal.id, status="unavailable",
                                  message="Booking details are only available for live Google Flights fares."))
    payload = _cached_details(deal)
    if payload is not None:
        return _from_payload(deal, payload)
    out = _usage(DealDetails(deal_id=deal.id, status="needs_fetch", cost=cost(deal)))
    if not serpapi.configured():
        out.message = "Live Google Flights search isn't configured on this server."
    elif out.left < out.cost:
        out.message = f"Needs {out.cost} live searches; {out.left} left this month."
    return out


_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _lock_for(ref: str) -> threading.Lock:
    with _locks_guard:
        return _locks[ref]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _expired(deal: StoredDeal, spent: int, return_leg: dict | None = None) -> DealDetails:
    payload = {"expired": True, "message": EXPIRED_MESSAGE, "fetched_at": _now_iso(),
               "return_leg": return_leg or None}
    _store_details(deal, payload)
    return _from_payload(deal, payload, spent)


def fetch_details(deal: StoredDeal) -> DealDetails:
    """Spend up to two live searches for the deal's return leg and booking
    options (see the module docstring). Concurrent fetches of the same deal
    wait for the first, then read its cached result."""
    if not available(deal):
        raise DetailsError(404, "Booking details are only available for live Google Flights fares.")
    with _lock_for(_ref(deal)):
        payload = _cached_details(deal)
        if payload is not None:
            return _from_payload(deal, payload)
        if not serpapi.configured():
            raise DetailsError(503, "Live Google Flights search isn't configured on this server.")
        need, left = cost(deal), serpapi.remaining()
        if left < need:
            raise DetailsError(409, f"Needs {need} live searches; only {left} left this month "
                                    f"(budget {serpapi.monthly_budget()}).")
        params = _params(deal)
        spent = 0
        return_leg: dict | None = None
        booking_token = deal.booking_token
        try:
            if not booking_token:
                returns, cached = serpapi.return_flights(params, deal.departure_token)
                spent += 0 if cached else 1
                match = serpapi.matching_return(returns, deal.price)
                if match is None:
                    return _expired(deal, spent)
                return_leg = serpapi.map_leg(match)
                booking_token = match.get("booking_token") or ""
                storage.set_deal_details(deal.id, booking_token, return_leg.get("depart_time", ""))
                if not booking_token:
                    return _expired(deal, spent, return_leg)
            data, cached = serpapi.booking_response(params, booking_token)
            spent += 0 if cached else 1
        except BudgetExhausted:
            raise DetailsError(409, f"This month's live search budget ({serpapi.monthly_budget()}) is used up.") \
                from None
        except ProviderError as exc:
            text = str(exc)
            if _TOKEN_ERROR.search(text) and not _API_KEY_ERROR.search(text):
                return _expired(deal, spent, return_leg)
            logger.warning("serpapi details failed: %s", text)
            raise DetailsError(502, "Google Flights didn't answer just now — try again in a minute.") from None

        options = serpapi.map_booking_options(data)
        selected = [serpapi.map_leg(it) for it in data.get("selected_flights") or []]
        selected = [leg for leg in selected if leg]
        if len(selected) > 1:
            return_leg = selected[1]
        if not options:
            return _expired(deal, spent, return_leg)
        payload = {
            "fetched_at": _now_iso(),
            "currency": str(params.get("currency") or deal.currency),
            "outbound": selected[0] if selected else None,
            "return_leg": return_leg if params.get("return_date") else None,
            "options": options,
        }
        _store_details(deal, payload)
        return _from_payload(deal, payload, spent)
