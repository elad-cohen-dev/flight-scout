"""Shared fixtures: an isolated temp SQLite DB and offline currency rates.

Nothing here touches backend/flight_scout.db, Turso, or any network service."""

from __future__ import annotations

import pytest

from app import bags, currency, storage


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
