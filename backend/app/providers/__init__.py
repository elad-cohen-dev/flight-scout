"""Fare-data providers behind the agent: real fares from flight APIs.

- `serpapi`: SerpApi Google Flights — live, exact dated fares (paid quota).
- `travelpayouts`: Aviasales Data API — free cached fares, calendars, explore.

Each exposes `configured()` and `search(FareQuery) -> ProviderResult`; app/engine.py
decides which to call. See base.py for the shared cache/usage plumbing.
"""

from .base import BudgetExhausted, FareQuery, ProviderError, ProviderResult

__all__ = ["BudgetExhausted", "FareQuery", "ProviderError", "ProviderResult"]
