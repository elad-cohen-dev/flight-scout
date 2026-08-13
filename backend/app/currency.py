"""Currency normalization and conversion for watch thresholds.

Web results quote prices in whatever currency the source page used (an Israeli
site says ILS, a deal blog says USD), while a watch threshold lives in one
currency. Comparing raw numbers across currencies silently drops real deals —
₪1,009 is *under* a $300 threshold, not 3× over it.

Rates come from frankfurter.app (free, keyless, ECB data), cached in-process
for 12h; if the fetch fails we fall back to a rough static table, which is fine
for a deal-alert threshold — being a few percent off beats not converting.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)

# Symbols and aliases the extractor occasionally emits instead of ISO codes.
_ALIASES = {
    "$": "USD", "US$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₪": "ILS", "NIS": "ILS",
}

# Approximate rates per 1 USD (ECB, 2026-08) — fallback only.
_FALLBACK_PER_USD = {
    "USD": 1.0, "EUR": 0.87, "GBP": 0.74, "ILS": 3.0,
    "CHF": 0.81, "CZK": 21.0, "PLN": 3.7, "HUF": 315.0, "RON": 4.4,
}

_RATES_URL = "https://api.frankfurter.dev/v1/latest?base=USD"
_RATES_TTL_SECONDS = 12 * 3600

_rates_lock = threading.Lock()
_rates: dict[str, float] | None = None
_rates_fetched_at = 0.0


def normalize(code: str) -> str:
    """'₪' -> 'ILS', 'usd ' -> 'USD'; unknown strings pass through uppercased."""
    code = (code or "").strip()
    return _ALIASES.get(code, code.upper())


def _fetch_rates() -> dict[str, float] | None:
    try:
        req = urllib.request.Request(_RATES_URL, headers={"User-Agent": "flight-scout/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        rates = {k.upper(): float(v) for k, v in data["rates"].items()}
        rates["USD"] = 1.0
        return rates
    except Exception as exc:
        logger.warning("rate fetch failed, using fallback table: %s", exc)
        return None


def _get_rates() -> dict[str, float]:
    global _rates, _rates_fetched_at
    with _rates_lock:
        now = time.monotonic()
        if _rates is None or now - _rates_fetched_at > _RATES_TTL_SECONDS:
            fetched = _fetch_rates()
            if fetched:
                _rates = fetched
                _rates_fetched_at = now
        # Live rates when we have them; fill anything missing from the table.
        return {**_FALLBACK_PER_USD, **(_rates or {})}


def convert(amount: float, from_code: str, to_code: str) -> float | None:
    """Convert `amount` between currencies; None when either code is unknown."""
    src, dst = normalize(from_code), normalize(to_code)
    if src == dst:
        return amount
    rates = _get_rates()
    if src not in rates or dst not in rates:
        return None
    return amount / rates[src] * rates[dst]
