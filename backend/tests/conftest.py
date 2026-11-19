"""Shared fixtures: a frozen clock, an isolated temp SQLite DB and offline
currency rates.

Nothing here touches backend/flight_scout.db, Turso, any network service — or
the real date."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import time_machine

from app import bags, currency, storage

# The moment every test runs at. The suite's fixtures are written around it —
# the London watch's "October - November" window, the November 2026 fares in
# the recorded SerpApi answers, "2026-11-05 to 2026-11-09" as a future trip —
# and much of the app asks the clock itself (date.today() behind an API route,
# "this month" in the usage counters), where no test can pass `today=` in.
NOW = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


@pytest.fixture(autouse=True)
def frozen_clock():
    """Run the test at NOW, in UTC, with time passing from there as usual.

    Without it the suite only passed for a couple of weeks around the day it
    was written: "mid October" became next year's, the watch's window shrank
    and its dated fares slid into the past. A test that needs another day
    passes `today=` / `now=` where the code takes one, or moves this clock:
    `frozen_clock.move_to(...)`."""
    # A ZoneInfo destination also pins the local timezone (date.today() is
    # local), so a laptop west of UTC sees the same day as the server does.
    with time_machine.travel(NOW.astimezone(ZoneInfo("UTC")), tick=True) as clock:
        yield clock


@pytest.fixture(autouse=True)
def offline_rates(monkeypatch):
    """Use currency's static fallback table instead of fetching live rates."""
    monkeypatch.setattr(currency, "_fetch_rates", lambda: None)
    monkeypatch.setattr(currency, "_rates", None)


@pytest.fixture(autouse=True)
def no_bag_table(tmp_path, monkeypatch):
    """Tests don't depend on the shipped bag-fee table (it changes every
    release): every carrier gets the typical range unless a test sets a table."""
    monkeypatch.setattr(bags, "TABLE_PATH", tmp_path / "no_bag_fees.json")
    bags.table.cache_clear()
    yield
    bags.table.cache_clear()


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    """LLM keys and model/price/budget overrides in a developer's .env must never
    reach a test (and no test may call a model); LLM tests set fakes explicitly."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FLIGHT_SCOUT_PROVIDER", "FLIGHT_SCOUT_MODEL",
                "FLIGHT_SCOUT_EXTRACT_MODEL", "FLIGHT_SCOUT_PRICES_JSON", "FLIGHT_SCOUT_AI_BUDGET_USD"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def no_fare_api_keys(monkeypatch):
    """Fare-API keys in a developer's .env must never route tests to the network;
    provider tests set fake keys explicitly."""
    for var in ("SERPAPI_API_KEY", "TRAVELPAYOUTS_TOKEN", "TRAVELPAYOUTS_MARKER",
                "SERPAPI_MONTHLY_BUDGET", "SERPAPI_RETURN_LEGS"):
        monkeypatch.delenv(var, raising=False)

    from app.providers import base

    def offline(url, *args, **kwargs):
        raise AssertionError(f"tests must not call fare APIs (GET {url.split('?')[0]})")

    # Second line of defence: provider HTTP refuses unless a test fakes it.
    monkeypatch.setattr(base.httpx, "get", offline)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.reset_state()
    yield tmp_path / "test.db"
    storage.reset_state()
