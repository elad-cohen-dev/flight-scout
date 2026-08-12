"""SQLite storage for found deals. One file, no ORM."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import FlightDeal, StoredDeal

DB_PATH = Path(__file__).resolve().parent.parent / "flight_scout.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_query TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    airline TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    return_date TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',
    stops INTEGER NOT NULL DEFAULT 0,
    booking_url TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    found_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deals_route ON deals (origin, destination);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def save_deals(search_query: str, deals: list[FlightDeal]) -> list[StoredDeal]:
    found_at = datetime.now(timezone.utc)
    stored: list[StoredDeal] = []
    with _connect() as conn:
        for deal in deals:
            cur = conn.execute(
                """INSERT INTO deals
                   (search_query, origin, destination, airline, price, currency,
                    depart_date, return_date, duration, stops, booking_url,
                    source_url, notes, found_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    search_query, deal.origin, deal.destination, deal.airline,
                    deal.price, deal.currency, deal.depart_date, deal.return_date,
                    deal.duration, deal.stops, deal.booking_url, deal.source_url,
                    deal.notes, found_at.isoformat(),
                ),
            )
            stored.append(
                StoredDeal(
                    id=cur.lastrowid,
                    search_query=search_query,
                    found_at=found_at,
                    **deal.model_dump(),
                )
            )
    return stored


def list_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 100,
) -> list[StoredDeal]:
    query = "SELECT * FROM deals WHERE 1=1"
    params: list = []
    if origin:
        query += " AND origin LIKE ?"
        params.append(f"%{origin}%")
    if destination:
        query += " AND destination LIKE ?"
        params.append(f"%{destination}%")
    if max_price is not None:
        query += " AND price <= ?"
        params.append(max_price)
    query += " ORDER BY found_at DESC, price ASC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [StoredDeal(**dict(row)) for row in rows]
