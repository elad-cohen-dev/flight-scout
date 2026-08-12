"""Deal storage: Turso (hosted libSQL) when configured, local SQLite otherwise.

Set TURSO_DATABASE_URL + TURSO_AUTH_TOKEN to use Turso — required on hosts with
ephemeral disks (e.g. Render's free tier). With neither set, falls back to a local
SQLite file, which is fine for development.

Both backends speak the sqlite3 DB-API, so the SQL below is shared. We avoid
sqlite3-only conveniences (row_factory, executescript, context-manager commit)
that libsql doesn't implement.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from collections import Counter, defaultdict

from .models import FlightDeal, HistoryPoint, RouteHistory, StoredDeal, Watch, WatchCreate

load_dotenv()

DB_PATH = Path(__file__).resolve().parent.parent / "flight_scout.db"

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS deals (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_deals_route ON deals (origin, destination)",
    """CREATE TABLE IF NOT EXISTS watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        origin TEXT NOT NULL,
        destination TEXT NOT NULL,
        dates_hint TEXT NOT NULL,
        max_price REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        direct_only INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_run_at TEXT,
        last_best_price REAL
    )""",
]

# Applied best-effort on connect; each fails harmlessly once the column exists.
_MIGRATIONS = [
    "ALTER TABLE watches ADD COLUMN direct_only INTEGER NOT NULL DEFAULT 0",
]


def _connect():
    turso_url = os.environ.get("TURSO_DATABASE_URL")
    if turso_url:
        import libsql

        conn = libsql.connect(database=turso_url, auth_token=os.environ.get("TURSO_AUTH_TOKEN"))
    else:
        conn = sqlite3.connect(DB_PATH)
    for statement in _SCHEMA:
        conn.execute(statement)
    for statement in _MIGRATIONS:
        try:
            conn.execute(statement)
        except Exception:
            pass  # column already exists
    conn.commit()
    return conn


def _rows_as_dicts(cursor) -> list[dict]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def save_deals(search_query: str, deals: list[FlightDeal]) -> list[StoredDeal]:
    found_at = datetime.now(timezone.utc)
    stored: list[StoredDeal] = []
    conn = _connect()
    try:
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
        conn.commit()
    finally:
        conn.close()
    return stored


def _fingerprint(row: dict) -> tuple:
    """Identity of a deal across search runs. A changed price is a *different*
    entry on purpose — repricing is information, not duplication."""
    return (
        row["origin"].strip().lower(),
        row["destination"].strip().lower(),
        row["airline"].strip().lower(),
        row["depart_date"].strip().lower(),
        row["return_date"].strip().lower(),
        row["currency"],
        round(row["price"], 2),
    )


def list_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 100,
    dedupe: bool = True,
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
    query += " ORDER BY found_at DESC, price ASC"
    if not dedupe:
        query += " LIMIT ?"
        params.append(limit)

    conn = _connect()
    try:
        rows = _rows_as_dicts(conn.execute(query, params))
    finally:
        conn.close()

    if not dedupe:
        return [StoredDeal(**row) for row in rows]

    # Rows are newest-first, so the first occurrence of a fingerprint is the
    # latest sighting — keep it, and fold older sightings into its counters.
    kept: dict[tuple, dict] = {}
    for row in rows:
        key = _fingerprint(row)
        if key in kept:
            kept[key]["times_seen"] += 1
            kept[key]["first_found_at"] = row["found_at"]  # rows keep getting older
        else:
            row["times_seen"] = 1
            kept[key] = row
    deals = [StoredDeal(**row) for row in list(kept.values())[:limit]]
    return deals


def _route_of(search_query: str) -> str:
    """'TLV -> London (anytime in October) [watch #4]' -> 'TLV -> London'.

    Grouping by the *requested* route keeps history stable even though extracted
    deals name specific airports (LTN/LHR/...) inconsistently across runs.
    """
    return search_query.split(" (")[0].strip()


def get_route_history() -> list[RouteHistory]:
    """Best price per route per search run, in each route's dominant currency.

    Prices in other currencies are excluded from the min rather than mixed in —
    a £95 and a $95 on one line would make the chart lie.
    """
    conn = _connect()
    try:
        rows = _rows_as_dicts(
            conn.execute("SELECT search_query, found_at, price, currency FROM deals")
        )
    finally:
        conn.close()

    by_route: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_route[_route_of(row["search_query"])].append(row)

    histories: list[RouteHistory] = []
    for route, route_rows in by_route.items():
        currency = Counter(r["currency"] for r in route_rows).most_common(1)[0][0]
        runs: dict[str, list[float]] = defaultdict(list)
        for r in route_rows:
            if r["currency"] == currency:
                runs[r["found_at"]].append(r["price"])
        points = [
            HistoryPoint(found_at=found_at, price=min(prices), deals=len(prices))
            for found_at, prices in sorted(runs.items())
        ]
        if points:
            histories.append(RouteHistory(route=route, currency=currency, points=points))

    histories.sort(key=lambda h: len(h.points), reverse=True)
    return histories


def _row_to_watch(row: dict) -> Watch:
    row["active"] = bool(row["active"])
    row["direct_only"] = bool(row.get("direct_only", 0))
    return Watch(**row)


def add_watch(watch: WatchCreate) -> Watch:
    created_at = datetime.now(timezone.utc)
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO watches (origin, destination, dates_hint, max_price, currency, direct_only, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (watch.origin, watch.destination, watch.dates_hint,
             watch.max_price, watch.currency, int(watch.direct_only), created_at.isoformat()),
        )
        conn.commit()
        watch_id = cur.lastrowid
    finally:
        conn.close()
    return Watch(id=watch_id, created_at=created_at, **watch.model_dump())


def list_watches(active_only: bool = False) -> list[Watch]:
    query = "SELECT * FROM watches"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY id"
    conn = _connect()
    try:
        rows = _rows_as_dicts(conn.execute(query))
    finally:
        conn.close()
    return [_row_to_watch(row) for row in rows]


def delete_watch(watch_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_watch_run(watch_id: int, best_price: float | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE watches SET last_run_at = ?, last_best_price = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), best_price, watch_id),
        )
        conn.commit()
    finally:
        conn.close()
