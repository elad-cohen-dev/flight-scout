"""Shared fixtures: an isolated temp SQLite DB and offline currency rates.

Nothing here touches backend/flight_scout.db, Turso, or any network service."""

from __future__ import annotations

import pytest

from app import currency, storage


@pytest.fixture(autouse=True)
def offline_rates(monkeypatch):
    """Use currency's static fallback table instead of fetching live rates."""
    monkeypatch.setattr(currency, "_fetch_rates", lambda: None)
    monkeypatch.setattr(currency, "_rates", None)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.reset_state()
    yield tmp_path / "test.db"
    storage.reset_state()
