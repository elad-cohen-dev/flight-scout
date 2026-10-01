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

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from collections import Counter, defaultdict

from . import airlines as _airlines
from . import airports as _airports
from .currency import convert as _convert_currency
from .currency import normalize as _normalize_currency
from .models import (
    AlertRecord, BookingLink, FlightDeal, HistoryPoint, PriceInsights, RouteHistory, SearchParams, StoredDeal,
    Watch, WatchCreate,
)

load_dotenv()

logger = logging.getLogger(__name__)

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
        found_at TEXT NOT NULL,
        origin_airport TEXT NOT NULL DEFAULT '',
        destination_airport TEXT NOT NULL DEFAULT '',
        depart_time TEXT NOT NULL DEFAULT '',
        arrive_time TEXT NOT NULL DEFAULT '',
        return_depart_time TEXT NOT NULL DEFAULT '',
        flight_numbers TEXT NOT NULL DEFAULT '',
        airline_code TEXT NOT NULL DEFAULT '',
        price_per_person REAL,
        price_confidence TEXT NOT NULL DEFAULT 'estimated',
        search_params TEXT,
        quality_flags TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        seen_at TEXT NOT NULL DEFAULT ''
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
        cabin_bag INTEGER NOT NULL DEFAULT 0,
        checked_bag INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_run_at TEXT,
        last_best_price REAL,
        search_params TEXT
    )""",
    # One row per alert actually sent; drives anti-spam and "last alert" on cards.
    """CREATE TABLE IF NOT EXISTS alert_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_id INTEGER NOT NULL,
        fingerprint TEXT NOT NULL,
        price REAL NOT NULL,
        currency TEXT NOT NULL,
        rule TEXT NOT NULL,
        score INTEGER,
        sent_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_alert_log_watch ON alert_log (watch_id, sent_at)",
    # Fare providers (app/providers): paid-call counter per calendar month,
    # raw-response cache, and the watch pre-screen decisions.
    """CREATE TABLE IF NOT EXISTS provider_usage (
        provider TEXT NOT NULL,
        month TEXT NOT NULL,
        calls INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (provider, month)
    )""",
    """CREATE TABLE IF NOT EXISTS provider_cache (
        cache_key TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS watch_prescreen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_id INTEGER NOT NULL,
        decided_at TEXT NOT NULL,
        live INTEGER NOT NULL,
        reason TEXT NOT NULL,
        cached_best REAL,
        currency TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_watch_prescreen ON watch_prescreen (watch_id, decided_at)",
    # Google Flights' price insights (typical range + price history) of a watch's
    # latest live search, one row per watch and comparable fare kind; they seed
    # the watch's stats until it has enough verified runs (app/alerts.py).
    """CREATE TABLE IF NOT EXISTS price_insights (
        watch_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        route TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL,
        depart_date TEXT NOT NULL DEFAULT '',
        return_date TEXT NOT NULL DEFAULT '',
        lowest_price REAL,
        price_level TEXT NOT NULL DEFAULT '',
        typical_low REAL,
        typical_high REAL,
        price_history TEXT NOT NULL DEFAULT '[]',
        seen_at TEXT NOT NULL,
        PRIMARY KEY (watch_id, kind)
    )""",
    # Background jobs (app/jobs.py), so a restart can't lose a search mid-flight:
    # the in-memory copy is the hot path, these rows are the durable record.
    """CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        query TEXT NOT NULL,
        request TEXT,
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status)",
    # A job's progress events, one row per flushed batch (a JSON array).
    """CREATE TABLE IF NOT EXISTS job_events (
        job_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        events TEXT NOT NULL,
        PRIMARY KEY (job_id, seq)
    )""",
]

# Applied best-effort at schema init; each fails harmlessly once the column exists.
_MIGRATIONS = [
    "ALTER TABLE watches ADD COLUMN direct_only INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE watches ADD COLUMN cabin_bag INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE watches ADD COLUMN checked_bag INTEGER NOT NULL DEFAULT 0",
    # v1.0: full search criteria (JSON-encoded SearchParams) + richer deal fields.
    "ALTER TABLE watches ADD COLUMN search_params TEXT",
    "ALTER TABLE deals ADD COLUMN origin_airport TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN destination_airport TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN depart_time TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN arrive_time TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN return_depart_time TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN flight_numbers TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN airline_code TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN price_per_person REAL",
    "ALTER TABLE deals ADD COLUMN price_confidence TEXT NOT NULL DEFAULT 'estimated'",
    "ALTER TABLE deals ADD COLUMN search_params TEXT",
    # v2.0: data-quality flags from normalize.normalize_deal (JSON list; '' = none).
    "ALTER TABLE deals ADD COLUMN quality_flags TEXT NOT NULL DEFAULT ''",
    # v2.0: per-watch alert rules (JSON-encoded AlertSettings; NULL = defaults).
    "ALTER TABLE watches ADD COLUMN alert_settings TEXT",
    # v2.0: fare provenance (app/providers): google_flights / aviasales / web.
    "ALTER TABLE deals ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN seen_at TEXT NOT NULL DEFAULT ''",
    # v2.1: SerpApi follow-up tokens + their request (app/details.py).
    "ALTER TABLE deals ADD COLUMN departure_token TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN booking_token TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE deals ADD COLUMN serp_params TEXT NOT NULL DEFAULT ''",
]

# Deal columns written by save_deals, beyond search_query/found_at/search_params.
_DEAL_COLUMNS = [
    "origin", "destination", "airline", "price", "currency", "depart_date",
    "return_date", "duration", "stops", "booking_url", "source_url", "notes",
    "origin_airport", "destination_airport", "depart_time", "arrive_time",
    "return_depart_time", "flight_numbers", "airline_code", "price_per_person",
    "price_confidence", "source", "seen_at", "departure_token", "booking_token", "serp_params",
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


def _parse_params(raw) -> SearchParams | None:
    """Decode a stored search_params JSON column; None for legacy/garbled rows."""
    if not raw:
        return None
    try:
        return SearchParams.model_validate_json(raw)
    except Exception:
        logger.warning("ignoring unreadable search_params: %.80s", raw)
        return None


def _booking_links(deal: FlightDeal, params: SearchParams | None) -> list[BookingLink]:
    """Links for a deal; a booking bug must never break saving or listing."""
    try:
        from .booking import build_links

        return build_links(deal, params)
    except Exception:
        logger.exception("build_links failed for %s -> %s", deal.origin, deal.destination)
        return []


def _legacy_params(deal: StoredDeal) -> SearchParams:
    """Best-effort criteria for rows saved before search_params existed: the
    flags search_label() appended to the query ("direct only", "cabin bag",
    "checked 20kg bag") are all the old rows carry, so links still honour them."""
    flags = deal.search_query.split(" (", 1)[-1].lower()
    return SearchParams(
        trip_type="round_trip" if deal.return_date.strip() else "one_way",
        max_stops=0 if ", direct only" in flags else None,
        cabin_bag=", cabin bag" in flags,
        checked_bag=", checked 20kg bag" in flags,
        currency=deal.currency,
    )


def _parse_flags(raw) -> list[str]:
    """Decode a stored quality_flags column ('' / NULL on rows never normalized)."""
    if not raw:
        return []
    try:
        flags = json.loads(raw)
    except Exception:
        return []
    return [str(f) for f in flags] if isinstance(flags, list) else []


def _row_to_deal(row: dict) -> StoredDeal:
    row = dict(row)
    params = _parse_params(row.pop("search_params", None))
    row["quality_flags"] = _parse_flags(row.get("quality_flags"))
    row["price_confidence"] = row.get("price_confidence") or "estimated"
    deal = StoredDeal(search_params=params, **row)
    # search_params stays None on legacy rows (the UI treats that as "parse the
    # label"); the inferred criteria only feed the booking links.
    link_params = params
    if link_params is None:
        try:
            link_params = _legacy_params(deal)
        except Exception:
            link_params = None
    deal.booking_links = _booking_links(deal, link_params)
    _set_compare_price(deal)
    deal.route_key = route_key(deal.search_query)
    deal.details_cost = _details_cost(deal)
    return deal


def _details_cost(deal: FlightDeal) -> int:
    """Upper bound of live searches for a deal's booking details (0 = none to fetch)."""
    if deal.source != "google_flights" or not deal.serp_params:
        return 0
    if deal.booking_token:
        return 1
    return 2 if deal.departure_token else 0


def _set_compare_price(deal: StoredDeal) -> None:
    """Sources quote whatever currency their page uses, so $600 vs ₪2,123 can't
    be ranked by raw number. Every deal also carries its price in the run's
    preferred currency; the UI sorts and picks "cheapest" by that."""
    target = deal.search_params.currency if deal.search_params else "USD"
    deal.compare_currency = target
    try:
        converted = _convert_currency(deal.price, deal.currency, target)
    except Exception:
        converted = None
    deal.compare_price = round(converted, 2) if converted is not None else None


def save_deals(search_query: str, deals: list[FlightDeal],
               params: SearchParams | None = None) -> list[StoredDeal]:
    found_at = datetime.now(timezone.utc)
    params_json = params.model_dump_json() if params is not None else None
    columns = ["search_query", *_DEAL_COLUMNS, "found_at", "search_params", "quality_flags"]
    sql = (f"INSERT INTO deals ({', '.join(columns)}) "
           f"VALUES ({', '.join('?' for _ in columns)})")

    def op(conn) -> list[StoredDeal]:
        stored: list[StoredDeal] = []
        for deal in deals:
            values = [getattr(deal, col) for col in _DEAL_COLUMNS]
            cur = conn.execute(sql, (search_query, *values, found_at.isoformat(), params_json,
                                     json.dumps(deal.quality_flags) if deal.quality_flags else ""))
            item = StoredDeal(
                id=cur.lastrowid,
                search_query=search_query,
                found_at=found_at,
                search_params=params,
                **deal.model_dump(exclude={"booking_links"}),
            )
            item.booking_links = _booking_links(item, params)
            _set_compare_price(item)
            item.route_key = route_key(item.search_query)
            item.departure_token, item.booking_token = deal.departure_token, deal.booking_token
            item.serp_params = deal.serp_params
            item.details_cost = _details_cost(deal)
            stored.append(item)
        conn.commit()
        return stored

    try:
        return _with_conn(op)
    finally:
        # Also clears history, which the UI re-fetches the moment a search ends.
        _invalidate_cache()


def get_deal(deal_id: int) -> StoredDeal | None:
    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,))))
    return _row_to_deal(rows[0]) if rows else None


def set_deal_details(deal_id: int, booking_token: str = "", return_depart_time: str = "") -> None:
    """Keep what a details fetch learned on the row: the return itinerary's
    booking token (the next fetch then costs one search, not two) and the
    return departure time when the row had none. Empty values change nothing."""
    def op(conn) -> None:
        conn.execute(
            "UPDATE deals SET booking_token = CASE WHEN ? != '' THEN ? ELSE booking_token END, "
            "return_depart_time = CASE WHEN return_depart_time = '' THEN ? ELSE return_depart_time END "
            "WHERE id = ?",
            (booking_token, booking_token, return_depart_time, deal_id),
        )
        conn.commit()

    try:
        _with_conn(op)
    finally:
        _invalidate_cache()


def _party_of(raw) -> tuple:
    """(adults, children, infants, cabin, trip type) of a row; legacy rows were 1 adult economy."""
    params = _parse_params(raw) or SearchParams()
    pax = params.passengers
    return (pax.adults, pax.children, pax.infants, params.cabin_class, params.trip_type)


def _place_key(text: str) -> str:
    try:
        place = _airports.resolve(text)
    except Exception:
        place = None
    return place.code if place else text.strip().lower()


def _airline_identity(name: str, code: str, flight_numbers: str) -> str:
    try:
        return _airlines.identity(name, code, flight_numbers)
    except Exception:
        return name.strip().lower()


def _fingerprint(row: dict) -> tuple:
    """Identity of a deal across search runs. A changed price is a *different*
    entry on purpose — repricing is information, not duplication. The party and
    cabin are part of it: the same total for 1 vs 3 travellers isn't the same deal.
    Places and carriers are compared by code, so 'TLV'/'Tel Aviv' and
    'EL AL'/'El Al'/'LY' sightings merge even on rows the repair script hasn't
    normalized."""
    return (
        _place_key(row["origin"]),
        _place_key(row["destination"]),
        _airline_identity(row["airline"], row.get("airline_code") or "", row.get("flight_numbers") or ""),
        row["depart_date"].strip().lower(),
        row["return_date"].strip().lower(),
        _normalize_currency(row["currency"]),  # "₪" and "ILS" are the same deal
        round(row["price"], 2),
        (row.get("flight_numbers") or "").replace(" ", "").lower(),
        _party_of(row.get("search_params")),
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
    # Normalized rows say "Tel Aviv" + origin_airport "TLV": match either.
    if origin:
        query += " AND (origin LIKE ? OR origin_airport LIKE ?)"
        params += [f"%{origin}%", f"%{origin}%"]
    if destination:
        query += " AND (destination LIKE ? OR destination_airport LIKE ?)"
        params += [f"%{destination}%", f"%{destination}%"]
    if max_price is not None:
        query += " AND price <= ?"
        params.append(max_price)
    query += " ORDER BY found_at DESC, price ASC"
    if not dedupe:
        query += " LIMIT ?"
        params.append(limit)

    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute(query, params)))

    if not dedupe:
        deals = [_row_to_deal(row) for row in rows]
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
    deals = [_row_to_deal(row) for row in list(kept.values())[:limit]]
    _cache_store(cache_key, deals, generation)
    return deals


def _route_of(search_query: str) -> str:
    """'TLV -> London (anytime in October) [watch #4]' -> 'TLV -> London'.

    Grouping by the *requested* route keeps history stable even though extracted
    deals name specific airports (LTN/LHR/...) inconsistently across runs.
    """
    return re.sub(r"\s*\[[^\]]*\]", "", search_query.split(" (")[0]).strip()


def route_key(search_query: str) -> str:
    """Canonical route id: each side resolved to an IATA code where possible
    ('Prague', 'PRG' and the autocomplete's 'Prague [PRG]' are one route),
    else its lowercased text. 'TLV -> Prague (October)' -> 'TLV>PRG'."""
    from .booking import _airport

    sides = search_query.split(" (")[0].split(" -> ")
    if len(sides) != 2:
        return _route_of(search_query).lower()
    keys = []
    for side in sides:
        try:
            code = _airport("", side.strip())
        except Exception:
            code = ""
        keys.append(code or re.sub(r"\s*\[[^\]]*\]", "", side).strip().lower())
    return ">".join(keys)


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
        by_route[route_key(row["search_query"])].append(row)

    histories: list[RouteHistory] = []
    for key, route_rows in by_route.items():
        # Display the most recent spelling of the route ("TLV -> Prague").
        route = _route_of(max(route_rows, key=lambda r: r["found_at"])["search_query"])
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
            histories.append(RouteHistory(route=route, route_key=key, currency=currency, points=points))

    histories.sort(key=lambda h: len(h.points), reverse=True)
    _cache_store(cache_key, histories, generation)
    return histories


# Watch fields that have their own (legacy) columns; everything else in
# SearchParams travels in the search_params JSON column.
_WATCH_COLUMNS = {"origin", "destination", "dates_hint", "max_price", "currency",
                  "direct_only", "cabin_bag", "checked_bag"}


def _row_to_watch(row: dict) -> Watch:
    row = dict(row)
    raw = row.pop("search_params", None)
    raw_alerts = row.pop("alert_settings", None)
    if raw_alerts:
        try:
            row["alerts"] = json.loads(raw_alerts)
        except Exception:
            logger.warning("watch #%s: ignoring unreadable alert_settings", row.get("id"))
    extra: dict = {}
    if raw:
        try:
            extra = {k: v for k, v in json.loads(raw).items() if k not in _WATCH_COLUMNS}
        except Exception:
            logger.warning("watch #%s: ignoring unreadable search_params", row.get("id"))
    row["active"] = bool(row["active"])
    for flag in ("direct_only", "cabin_bag", "checked_bag"):
        row[flag] = bool(row.get(flag, 0))
    return Watch(**extra, **row)


def _watch_params_json(watch: WatchCreate) -> str:
    return json.dumps({
        k: v for k, v in watch.model_dump(mode="json", include=set(SearchParams.model_fields)).items()
        if k not in _WATCH_COLUMNS
    })


def add_watch(watch: WatchCreate) -> Watch:
    created_at = datetime.now(timezone.utc)
    params_json = _watch_params_json(watch)

    def op(conn) -> int:
        cur = conn.execute(
            """INSERT INTO watches (origin, destination, dates_hint, max_price, currency,
                                    direct_only, cabin_bag, checked_bag, active, created_at,
                                    search_params, alert_settings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (watch.origin, watch.destination, watch.dates_hint,
             watch.max_price, watch.currency, int(watch.direct_only),
             int(watch.cabin_bag), int(watch.checked_bag), created_at.isoformat(),
             params_json, watch.alerts.model_dump_json()),
        )
        conn.commit()
        return cur.lastrowid

    try:
        watch_id = _with_conn(op)
    finally:
        _invalidate_cache()
    return Watch(id=watch_id, created_at=created_at, **watch.model_dump())


def get_watch(watch_id: int) -> Watch | None:
    return next((w for w in list_watches() if w.id == watch_id), None)


def update_watch(watch_id: int, watch: WatchCreate, active: bool) -> Watch | None:
    """Replace a watch's criteria/threshold and active flag; None if it doesn't exist.

    The route stays put, so its "[watch #N]" history keeps accruing under the
    same id. When the search itself changes (anything beyond the threshold or
    pausing), last_run_at/last_best_price are cleared: they described results
    for the old criteria (or another currency) and would mislead until the next run."""
    current = get_watch(watch_id)
    if current is None:
        return None
    same_search = current.model_dump(exclude={"max_price", "alerts"}, include=set(WatchCreate.model_fields)) == \
        watch.model_dump(exclude={"max_price", "alerts"}, include=set(WatchCreate.model_fields))
    last_run_at = current.last_run_at.isoformat() if same_search and current.last_run_at else None
    last_best_price = current.last_best_price if same_search else None

    def op(conn) -> int:
        cur = conn.execute(
            """UPDATE watches SET dates_hint = ?, max_price = ?, currency = ?, direct_only = ?,
                                  cabin_bag = ?, checked_bag = ?, active = ?, search_params = ?,
                                  last_run_at = ?, last_best_price = ?, alert_settings = ?
               WHERE id = ?""",
            (watch.dates_hint, watch.max_price, watch.currency, int(watch.direct_only),
             int(watch.cabin_bag), int(watch.checked_bag), int(active), _watch_params_json(watch),
             last_run_at, last_best_price, watch.alerts.model_dump_json(), watch_id),
        )
        conn.commit()
        return cur.rowcount

    try:
        if not _with_conn(op):
            return None
    finally:
        _invalidate_cache()
    return get_watch(watch_id)


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
        conn.execute("DELETE FROM price_insights WHERE watch_id = ?", (watch_id,))
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


# --- watch sightings + alert log (smart alerts, see app/alerts.py) -----------

_WATCH_TAG = re.compile(r"\[watch #(\d+)\]\s*$")


def watch_sightings() -> dict[int, list[dict]]:
    """Every stored deal of every watch's runs, as light rows (found_at, price,
    currency, search_params, source) keyed by watch id — the input of the price
    stats (source decides the row's trust tier).
    A watch's runs are tagged "[watch #N]" at the end of their search label."""
    cache_key = ("watch_sightings",)
    cached, generation = _cache_read(cache_key)
    if cached is not None:
        return dict(cached)

    rows = _with_conn(
        lambda conn: _rows_as_dicts(conn.execute(
            "SELECT search_query, found_at, price, currency, search_params, source FROM deals "
            "WHERE search_query LIKE '%[watch #%'"
        ))
    )
    by_watch: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        m = _WATCH_TAG.search(row["search_query"])
        if m:
            by_watch[int(m.group(1))].append(row)
    # The cache stores lists; a list of pairs round-trips through dict().
    _cache_store(cache_key, list(by_watch.items()), generation)
    return dict(by_watch)


def watch_run_deals(watch_id: int) -> list[StoredDeal]:
    """The deals of a watch's most recent run (with booking links)."""
    rows = _with_conn(
        lambda conn: _rows_as_dicts(conn.execute(
            "SELECT * FROM deals WHERE search_query LIKE ? ORDER BY found_at DESC",
            (f"%[watch #{watch_id}]",),
        ))
    )
    if not rows:
        return []
    latest = rows[0]["found_at"]
    return [_row_to_deal(row) for row in rows if row["found_at"] == latest]


def log_alert(record: AlertRecord) -> None:
    def op(conn) -> None:
        conn.execute(
            "INSERT INTO alert_log (watch_id, fingerprint, price, currency, rule, score, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record.watch_id, record.fingerprint, record.price, record.currency, record.rule,
             record.score, record.sent_at.isoformat()),
        )
        conn.commit()

    try:
        _with_conn(op)
    finally:
        _invalidate_cache()


def list_alerts(watch_id: int | None = None, since: datetime | None = None) -> list[AlertRecord]:
    """Sent alerts, newest first; optionally one watch's and/or since a time."""
    cache_key = ("alerts", watch_id, since.isoformat() if since else None)
    cached, generation = _cache_read(cache_key)
    if cached is not None:
        return cached

    query = "SELECT watch_id, fingerprint, price, currency, rule, score, sent_at FROM alert_log WHERE 1=1"
    params: list = []
    if watch_id is not None:
        query += " AND watch_id = ?"
        params.append(watch_id)
    if since is not None:
        query += " AND sent_at >= ?"
        params.append(since.isoformat())
    query += " ORDER BY sent_at DESC, id DESC"
    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute(query, params)))
    alerts = [AlertRecord(**row) for row in rows]
    _cache_store(cache_key, alerts, generation)
    return alerts


_INSIGHT_COLUMNS = ["route", "currency", "depart_date", "return_date", "lowest_price", "price_level",
                    "typical_low", "typical_high"]


def save_price_insights(watch_id: int, kind: str, insights: PriceInsights,
                        seen_at: datetime | None = None) -> None:
    """Keep Google's latest price insights for a watch's fare `kind`
    (alerts.insight_kind), replacing the previous ones."""
    seen_at = seen_at or datetime.now(timezone.utc)
    columns = ["watch_id", "kind", *_INSIGHT_COLUMNS, "price_history", "seen_at"]
    values = [watch_id, kind, *(getattr(insights, c) for c in _INSIGHT_COLUMNS),
              json.dumps(insights.price_history), seen_at.isoformat()]

    def op(conn) -> None:
        conn.execute(f"INSERT OR REPLACE INTO price_insights ({', '.join(columns)}) "
                     f"VALUES ({', '.join('?' for _ in columns)})", values)
        conn.commit()

    try:
        _with_conn(op)
    finally:
        _invalidate_cache()


def watch_price_insights() -> dict[int, list[dict]]:
    """Stored price insights keyed by watch id: rows with the PriceInsights
    fields (price_history decoded) plus kind and seen_at."""
    cache_key = ("price_insights",)
    cached, generation = _cache_read(cache_key)
    if cached is not None:
        return dict(cached)
    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute("SELECT * FROM price_insights")))
    by_watch: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        try:
            row["price_history"] = json.loads(row["price_history"] or "[]")
        except Exception:
            row["price_history"] = []
        by_watch[int(row["watch_id"])].append(row)
    _cache_store(cache_key, list(by_watch.items()), generation)
    return dict(by_watch)


# --- fare providers: usage counter, response cache, watch pre-screen log -------
#
# Not part of the read cache above: these tables are read on every provider call
# and written after it, so memoizing them would only serve stale budgets, and
# their writes don't touch deals/watches, so they don't invalidate it either.

def _month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def provider_calls(provider: str, now: datetime | None = None) -> int:
    """Paid calls `provider` made this calendar month (UTC)."""
    rows = _with_conn(lambda conn: conn.execute(
        "SELECT calls FROM provider_usage WHERE provider = ? AND month = ?",
        (provider, _month(now)),
    ).fetchall())
    return int(rows[0][0]) if rows else 0


def record_provider_call(provider: str, now: datetime | None = None) -> int:
    """Count one paid call; returns this month's new total."""
    month = _month(now)

    def op(conn) -> int:
        conn.execute(
            "INSERT INTO provider_usage (provider, month, calls) VALUES (?, ?, 1) "
            "ON CONFLICT (provider, month) DO UPDATE SET calls = calls + 1",
            (provider, month),
        )
        conn.commit()
        rows = conn.execute("SELECT calls FROM provider_usage WHERE provider = ? AND month = ?",
                            (provider, month)).fetchall()
        return int(rows[0][0])

    return _with_conn(op)


def cache_get(cache_key: str, now: datetime | None = None) -> dict | None:
    """A provider's cached raw response, or None when missing or expired."""
    now = now or datetime.now(timezone.utc)
    rows = _with_conn(lambda conn: conn.execute(
        "SELECT payload FROM provider_cache WHERE cache_key = ? AND expires_at > ?",
        (cache_key, now.isoformat()),
    ).fetchall())
    if not rows:
        return None
    try:
        return json.loads(rows[0][0])
    except Exception:
        return None


def cache_put(cache_key: str, provider: str, payload: dict, ttl_seconds: float,
              now: datetime | None = None) -> None:
    """Store a raw response for `ttl_seconds`; also prunes expired entries."""
    now = now or datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)

    def op(conn) -> None:
        conn.execute("DELETE FROM provider_cache WHERE expires_at <= ?", (now.isoformat(),))
        conn.execute(
            "INSERT OR REPLACE INTO provider_cache (cache_key, provider, payload, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cache_key, provider, json.dumps(payload), now.isoformat(), expires.isoformat()),
        )
        conn.commit()

    _with_conn(op)


def log_prescreen(watch_id: int, live: bool, reason: str, cached_best: float | None,
                  currency: str, now: datetime | None = None) -> None:
    """Record a watch pre-screen decision (spend a live search or not, and why)."""
    now = now or datetime.now(timezone.utc)

    def op(conn) -> None:
        conn.execute(
            "INSERT INTO watch_prescreen (watch_id, decided_at, live, reason, cached_best, currency) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (watch_id, now.isoformat(), int(live), reason, cached_best, currency),
        )
        conn.commit()

    _with_conn(op)


def last_live_check(watch_id: int) -> datetime | None:
    """When the pre-screen last spent a live search on this watch."""
    rows = _with_conn(lambda conn: conn.execute(
        "SELECT MAX(decided_at) FROM watch_prescreen WHERE watch_id = ? AND live = 1", (watch_id,),
    ).fetchall())
    if not rows or not rows[0][0]:
        return None
    return datetime.fromisoformat(rows[0][0])


def list_prescreens(watch_id: int | None = None, limit: int = 50) -> list[dict]:
    """Pre-screen decisions, newest first."""
    query = "SELECT watch_id, decided_at, live, reason, cached_best, currency FROM watch_prescreen"
    params: list = []
    if watch_id is not None:
        query += " WHERE watch_id = ?"
        params.append(watch_id)
    query += " ORDER BY decided_at DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute(query, params)))
    for row in rows:
        row["live"] = bool(row["live"])
    return rows


# --- background jobs ----------------------------------------------------------
#
# Durable copies of app/jobs.py's in-memory jobs. Never cached: a job is read
# from here only when it isn't in memory (after a restart), and its writes don't
# touch deals/watches, so they leave the read cache alone too.

_JOB_COLUMNS = ["job_id", "kind", "status", "query", "request", "result", "error",
                "created_at", "updated_at"]


def save_job(row: dict) -> None:
    """Insert or update one job row (`request` / `result` already JSON text)."""
    sql = (f"INSERT INTO jobs ({', '.join(_JOB_COLUMNS)}) "
           f"VALUES ({', '.join('?' for _ in _JOB_COLUMNS)}) "
           "ON CONFLICT (job_id) DO UPDATE SET status = excluded.status, "
           "result = excluded.result, error = excluded.error, updated_at = excluded.updated_at")

    def op(conn) -> None:
        conn.execute(sql, [row.get(col) for col in _JOB_COLUMNS])
        conn.commit()

    _with_conn(op)


def get_job(job_id: str) -> dict | None:
    rows = _with_conn(lambda conn: _rows_as_dicts(conn.execute(
        f"SELECT {', '.join(_JOB_COLUMNS)} FROM jobs WHERE job_id = ?", (job_id,))))
    return rows[0] if rows else None


def append_job_events(job_id: str, seq: int, events: list[dict]) -> None:
    """Store one batch of a job's progress events; `seq` orders the batches."""
    def op(conn) -> None:
        conn.execute("INSERT OR REPLACE INTO job_events (job_id, seq, events) VALUES (?, ?, ?)",
                     (job_id, seq, json.dumps(events)))
        conn.commit()

    _with_conn(op)


def get_job_events(job_id: str) -> list[dict]:
    """Every stored event of a job, in emission order."""
    rows = _with_conn(lambda conn: conn.execute(
        "SELECT events FROM job_events WHERE job_id = ? ORDER BY seq", (job_id,)).fetchall())
    events: list[dict] = []
    for (raw,) in rows:
        try:
            events.extend(json.loads(raw))
        except Exception:
            logger.warning("ignoring unreadable job_events batch for %s", job_id)
    return events


def interrupt_running_jobs(search_message: str, watch_message: str,
                           now: datetime | None = None, keep_events_days: int = 7,
                           keep_jobs_days: int = 30) -> list[str]:
    """Mark jobs a dead process left "running" as "interrupted"; returns their ids.

    Also trims old history in the same round trip budget: events go after
    `keep_events_days`, job rows after `keep_jobs_days`.
    """
    now = now or datetime.now(timezone.utc)
    events_cutoff = (now - timedelta(days=keep_events_days)).isoformat()
    jobs_cutoff = (now - timedelta(days=keep_jobs_days)).isoformat()

    def op(conn) -> list[str]:
        ids = [r[0] for r in conn.execute("SELECT job_id FROM jobs WHERE status = 'running'").fetchall()]
        if ids:
            conn.execute(
                "UPDATE jobs SET status = 'interrupted', updated_at = ?, "
                "error = CASE WHEN kind = 'watch_run' THEN ? ELSE ? END WHERE status = 'running'",
                (now.isoformat(), watch_message, search_message),
            )
        conn.execute("DELETE FROM job_events WHERE job_id IN "
                     "(SELECT job_id FROM jobs WHERE updated_at < ?)", (events_cutoff,))
        conn.execute("DELETE FROM jobs WHERE updated_at < ?", (jobs_cutoff,))
        conn.commit()
        return ids

    return _with_conn(op)
