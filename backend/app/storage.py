"""Deal storage: Turso (hosted libSQL) when configured, local SQLite otherwise.

Set TURSO_DATABASE_URL + TURSO_AUTH_TOKEN to use Turso — required on hosts with
ephemeral disks (e.g. Render's free tier). With neither set, falls back to a local
SQLite file, which is fine for development.

Both backends speak the sqlite3 DB-API, so the SQL below is shared. We avoid
sqlite3-only conveniences (row_factory, executescript, context-manager commit)
that libsql doesn't implement.

Every statement goes through `_with_conn`, which reuses one process-wide
connection under a lock. Against Turso each connect is a network round trip and
the schema DDL is several more, so doing that per request cost seconds; now it
happens once per process. Reads are additionally served from a short-lived
in-process cache that every write clears.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from collections import Counter, defaultdict

from .currency import convert as _convert_currency
from .currency import normalize as _normalize_currency
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

# Applied best-effort at schema init; each fails harmlessly once the column exists.
_MIGRATIONS = [
    "ALTER TABLE watches ADD COLUMN direct_only INTEGER NOT NULL DEFAULT 0",
]


# --- connection ------------------------------------------------------------
#
# One connection per process, serialized by a lock. Writes run in background
# job threads while requests read, so the connection is shared across threads:
# both drivers are told not to enforce same-thread use, and the lock provides
# the mutual exclusion they would otherwise rely on that check for. The tables
# hold hundreds of rows, so serializing every statement costs nothing next to
# the round trip we save.

_db_lock = threading.RLock()
_conn = None
_schema_ready = False


def _new_conn():
    turso_url = os.environ.get("TURSO_DATABASE_URL")
    if turso_url:
        import libsql

        return libsql.connect(
            database=turso_url,
            # libsql rejects a None auth_token outright; "" is its own default.
            auth_token=os.environ.get("TURSO_AUTH_TOKEN") or "",
            _check_same_thread=False,
        )
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _init_schema(conn) -> None:
    for statement in _SCHEMA:
        conn.execute(statement)
    for statement in _MIGRATIONS:
        try:
            conn.execute(statement)
        except Exception:
            pass  # column already exists
    conn.commit()


def _is_fatal(exc: BaseException) -> bool:
    """Interpreter-level signals, which must never be mistaken for a bad connection."""
    return isinstance(exc, (KeyboardInterrupt, SystemExit))


def _discard_conn() -> None:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except BaseException as exc:  # noqa: BLE001 - see _with_conn on libsql panics
            if _is_fatal(exc):
                raise
        _conn = None


def _with_conn(operation):
    """Run `operation(conn)` on the shared connection, holding the lock throughout.

    The schema is created on the first connection of the process only — it is a
    property of the database, not of the connection, so later reconnects skip it.

    Turso hangs up on idle connections, which surfaces as an error on the first
    statement after the idle period. A failure on a *reused* connection is
    therefore retried once on a fresh one; a failure on a connection we just
    opened is real and propagates. Retrying is safe because every operation here
    commits exactly once at the end, so a failure before that commit leaves
    nothing behind on the dead connection.

    The catch is BaseException on purpose: libsql is a Rust extension, and using
    a connection it has already torn down raises pyo3's PanicException, which
    derives from BaseException and would slip straight past `except Exception` —
    that is, past the retry written for it. Real interrupts are re-raised.
    """
    global _conn, _schema_ready
    with _db_lock:
        while True:
            reused = _conn is not None
            if _conn is None:
                _conn = _new_conn()
                if not _schema_ready:
                    _init_schema(_conn)
                    _schema_ready = True
            try:
                return operation(_conn)
            except BaseException as exc:  # noqa: BLE001
                if _is_fatal(exc):
                    raise
                _discard_conn()
                if not reused:
                    raise


# --- read cache -------------------------------------------------------------
#
# The UI fetches deals, watches and history back to back on every load and again
# after each search, and the data only changes through this module. So reads are
# memoized briefly and every write clears the lot — coarse, but it means no
# caller has to reason about which key its write touched.

_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 64

_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[float, list]] = {}
_cache_generation = 0


def _cache_read(key: tuple) -> tuple[list | None, int]:
    """Return (cached value or None, the generation the read was made against)."""
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            return list(entry[1]), _cache_generation
        return None, _cache_generation


def _cache_store(key: tuple, value: list, generation: int) -> None:
    """Store `value` unless a write landed since the query that produced it.

    Without the generation check a slow reader could overwrite a fresh
    invalidation with rows it fetched before the write, and serve them for a
    whole TTL.
    """
    with _cache_lock:
        if generation != _cache_generation:
            return
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            _cache.clear()
        _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, list(value))


def _invalidate_cache() -> None:
    global _cache_generation
    with _cache_lock:
        _cache.clear()
        _cache_generation += 1


def reset_state() -> None:
    """Drop the pooled connection and every cached read.

    Needed after DB_PATH changes (tests) — nothing in the app calls it.
    """
    global _schema_ready
    with _db_lock:
        _discard_conn()
        _schema_ready = False
    _invalidate_cache()


def _rows_as_dicts(cursor) -> list[dict]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def save_deals(search_query: str, deals: list[FlightDeal]) -> list[StoredDeal]:
    found_at = datetime.now(timezone.utc)

    def op(conn) -> list[StoredDeal]:
        stored: list[StoredDeal] = []
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
        return stored

    try:
        return _with_conn(op)
    finally:
        # Also clears history, which the UI re-fetches the moment a search ends.
        _invalidate_cache()


def _fingerprint(row: dict) -> tuple:
    """Identity of a deal across search runs. A changed price is a *different*
    entry on purpose — repricing is information, not duplication."""
    return (
        row["origin"].strip().lower(),
        row["destination"].strip().lower(),
        row["airline"].strip().lower(),
        row["depart_date"].strip().lower(),
        row["return_date"].strip().lower(),
        _normalize_currency(row["currency"]),  # "₪" and "ILS" are the same deal
        round(row["price"], 2),
    )


def list_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 100,
    dedupe: bool = True,
) -> list[StoredDeal]:
    cache_key = ("deals", origin, destination, max_price, limit, dedupe)
    cached, generation = _cache_read(cache_key)
    if cached is not None:
        return cached

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

    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute(query, params)))

    if not dedupe:
        deals = [StoredDeal(**row) for row in rows]
        _cache_store(cache_key, deals, generation)
        return deals

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
    _cache_store(cache_key, deals, generation)
    return deals


def _route_of(search_query: str) -> str:
    """'TLV -> London (anytime in October) [watch #4]' -> 'TLV -> London'.

    Grouping by the *requested* route keeps history stable even though extracted
    deals name specific airports (LTN/LHR/...) inconsistently across runs.
    """
    return search_query.split(" (")[0].strip()


def get_route_history() -> list[RouteHistory]:
    """Best price per route per search run, in each route's dominant currency.

    Web results quote whatever currency the source page used, so a route's runs
    mix USD/ILS/EUR freely. Every price is converted into the route's dominant
    currency before taking the per-run min — dropping other currencies instead
    (the old behaviour) meant a run's real cheapest fare could be invisible and
    the chart would flatline on whichever fare happened to share the dominant
    currency. Conversion uses today's rates for historical points, which is fine
    at deal-alert precision. Rows in a currency we can't convert are skipped.
    """
    cache_key = ("history",)
    cached, generation = _cache_read(cache_key)
    if cached is not None:
        return cached

    rows = _with_conn(
        lambda conn: _rows_as_dicts(
            conn.execute("SELECT search_query, found_at, price, currency FROM deals")
        )
    )

    by_route: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        row["currency"] = _normalize_currency(row["currency"])
        by_route[_route_of(row["search_query"])].append(row)

    histories: list[RouteHistory] = []
    for route, route_rows in by_route.items():
        currency = Counter(r["currency"] for r in route_rows).most_common(1)[0][0]
        runs: dict[str, list[float]] = defaultdict(list)
        for r in route_rows:
            price = _convert_currency(r["price"], r["currency"], currency)
            if price is not None:
                runs[r["found_at"]].append(round(price, 2))
        points = [
            HistoryPoint(found_at=found_at, price=min(prices), deals=len(prices))
            for found_at, prices in sorted(runs.items())
        ]
        if points:
            histories.append(RouteHistory(route=route, currency=currency, points=points))

    histories.sort(key=lambda h: len(h.points), reverse=True)
    _cache_store(cache_key, histories, generation)
    return histories


def _row_to_watch(row: dict) -> Watch:
    row["active"] = bool(row["active"])
    row["direct_only"] = bool(row.get("direct_only", 0))
    return Watch(**row)


def add_watch(watch: WatchCreate) -> Watch:
    created_at = datetime.now(timezone.utc)

    def op(conn) -> int:
        cur = conn.execute(
            """INSERT INTO watches (origin, destination, dates_hint, max_price, currency, direct_only, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (watch.origin, watch.destination, watch.dates_hint,
             watch.max_price, watch.currency, int(watch.direct_only), created_at.isoformat()),
        )
        conn.commit()
        return cur.lastrowid

    try:
        watch_id = _with_conn(op)
    finally:
        _invalidate_cache()
    return Watch(id=watch_id, created_at=created_at, **watch.model_dump())


def list_watches(active_only: bool = False) -> list[Watch]:
    cache_key = ("watches", active_only)
    cached, generation = _cache_read(cache_key)
    if cached is not None:
        return cached

    query = "SELECT * FROM watches"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY id"
    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute(query)))
    watches = [_row_to_watch(row) for row in rows]
    _cache_store(cache_key, watches, generation)
    return watches


def delete_watch(watch_id: int) -> bool:
    def op(conn) -> bool:
        cur = conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        conn.commit()
        return cur.rowcount > 0

    try:
        return _with_conn(op)
    finally:
        _invalidate_cache()


def update_watch_run(watch_id: int, best_price: float | None) -> None:
    def op(conn) -> None:
        conn.execute(
            "UPDATE watches SET last_run_at = ?, last_best_price = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), best_price, watch_id),
        )
        conn.commit()

    try:
        _with_conn(op)
    finally:
        _invalidate_cache()
