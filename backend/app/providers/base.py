"""Shared plumbing for fare-data providers: the query/result types, the one
network entry point, the response cache and the monthly usage counter.

A provider turns a `FareQuery` (concrete IATA codes + dates + SearchParams)
into `FlightDeal`s. Every HTTP call goes through `http_get_json`, so tests
patch exactly one function and no API key can leak into an error message.
Responses are cached raw (keyed by a hash of the normalized request, minus
credentials) so mapping fixes apply to cached data too.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

import httpx

from .. import storage
from ..models import FlightDeal, PriceInsights, SearchParams

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 45  # SerpApi live searches take ~3-15s; deep searches longer


class ProviderError(Exception):
    """A provider call failed (network, HTTP status, API error). Never carries the key."""


class BudgetExhausted(ProviderError):
    """The monthly paid-call budget is used up; callers fall back to free sources."""


@dataclass(frozen=True, eq=False)
class FareQuery:
    """One concrete provider request.

    `origin`/`destination` are IATA airport or city codes (SerpApi also takes a
    comma list of airports). `depart_date` is YYYY-MM-DD, or YYYY-MM for
    calendar-style Travelpayouts queries; `return_date` is '' for one-way."""

    origin: str
    destination: str
    depart_date: str
    return_date: str = ""
    params: SearchParams = field(default_factory=SearchParams)


@dataclass
class ProviderResult:
    deals: list[FlightDeal] = field(default_factory=list)
    insights: PriceInsights | None = None
    paid_calls: int = 0  # calls that counted against the provider's quota
    cached: bool = False  # served from our response cache
    departure_tokens: dict[int, str] = field(default_factory=dict)  # SerpApi round trips: deal index -> token


def http_get_json(url: str, params: dict, headers: dict | None = None, *, provider: str) -> dict:
    """GET a JSON document. Errors are re-raised as ProviderError with the
    status and a short body excerpt, but never the request URL (it can hold
    the API key)."""
    try:
        resp = httpx.get(url, params=params, headers=headers or {}, timeout=HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise ProviderError(f"{provider}: {type(exc).__name__}") from None
    if resp.status_code != 200:
        excerpt = resp.text[:200].replace("\n", " ")
        raise ProviderError(f"{provider}: HTTP {resp.status_code} {excerpt}")
    try:
        data = resp.json()
    except ValueError:
        raise ProviderError(f"{provider}: response is not JSON") from None
    if not isinstance(data, dict):
        raise ProviderError(f"{provider}: unexpected response shape")
    return data


_SECRET_PARAMS = {"api_key", "token"}


def cache_key(provider: str, params: dict) -> str:
    """Stable hash of a request: credentials dropped, keys sorted, values as strings."""
    clean = {k: str(v) for k, v in params.items() if k not in _SECRET_PARAMS and v not in (None, "")}
    blob = json.dumps([provider, sorted(clean.items())], separators=(",", ":"))
    return f"{provider}:{hashlib.sha256(blob.encode()).hexdigest()[:40]}"


def cached_get(provider: str, url: str, params: dict, headers: dict | None = None,
               *, budget_left=None, fresh: bool = False) -> tuple[dict, bool]:
    """(response, from_cache). A cache miss spends one call: `budget_left()`
    (when given) must be positive first, else BudgetExhausted; the call is
    counted in provider_usage once the provider answered. The caller caches
    the response with `store` once it knows it is worth keeping. `fresh`
    skips the cache read (a deliberate recheck), never the budget."""
    key = cache_key(provider, params)
    hit = None
    try:
        hit = None if fresh else storage.cache_get(key)
    except Exception:
        logger.exception("%s: cache read failed", provider)
    if hit is not None:
        return hit, True
    if budget_left is not None and budget_left() <= 0:
        raise BudgetExhausted(f"{provider}: monthly budget used up")
    data = http_get_json(url, params, headers, provider=provider)
    try:
        storage.record_provider_call(provider)
    except Exception:
        logger.exception("%s: usage counter update failed", provider)
    return data, False


def store(provider: str, params: dict, payload: dict, ttl_seconds: float) -> None:
    """Cache a (possibly trimmed) response; a cache failure never fails a search."""
    try:
        storage.cache_put(cache_key(provider, params), provider, payload, ttl_seconds)
    except Exception:
        logger.exception("%s: cache write failed", provider)


def paying(params: SearchParams) -> int:
    """Travellers who pay a seat fare (lap infants don't)."""
    return max(params.passengers.adults + params.passengers.children, 1)


def fmt_minutes(minutes) -> str:
    """135 -> '2h 15m'; '' when unknown."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return ""
    if m <= 0:
        return ""
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"
