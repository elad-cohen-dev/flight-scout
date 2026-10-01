"""One-time cleanup of stored deals with app/normalize.py — dry run by default.

    cd backend && uv run python scripts/repair_deals.py               # preview (reads only)
    cd backend && uv run python scripts/repair_deals.py --apply       # write the changes
    cd backend && uv run python scripts/repair_deals.py --db /tmp/x.db  # a specific SQLite file

Target: Turso when TURSO_DATABASE_URL (+ TURSO_AUTH_TOKEN) is set — in the
environment or backend/.env, exactly as the app connects — else the local
SQLite file. `--db` forces a SQLite file and ignores Turso. The target is
printed first; check it before passing --apply.

What it does: re-derives the normalized fields (airline + airline_code, origin/
destination + airport codes, currency, dates, times, flight numbers) and
quality_flags of every row, prints counts per change type and sample before/
after rows, and with --apply UPDATEs only the rows that changed. It never
deletes rows and never touches price, so it is safe to re-run: a second run
finds nothing to change. The dry run doesn't even run the schema migrations;
--apply does (adding the quality_flags column if missing), as app startup would.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storage  # noqa: E402  (loads backend/.env)
from app.models import FlightDeal  # noqa: E402
from app.normalize import normalize_deal  # noqa: E402

# Columns the repair may rewrite. price/price_per_person never change.
REPAIR_COLUMNS = [
    "airline", "airline_code", "origin", "destination", "origin_airport", "destination_airport",
    "currency", "depart_date", "return_date", "depart_time", "arrive_time", "return_depart_time",
    "flight_numbers", "stops", "notes", "quality_flags",
]
SAMPLES = 15


def _found_on(raw) -> date | None:
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


def _deal_of(row: dict) -> FlightDeal:
    fields = {k: row[k] for k in FlightDeal.model_fields if k in row and row[k] is not None}
    fields["quality_flags"] = storage._parse_flags(row.get("quality_flags"))
    fields["price_confidence"] = row.get("price_confidence") or "estimated"
    fields.pop("booking_links", None)
    return FlightDeal(**fields)


def plan(rows: list[dict]) -> list[tuple[dict, dict]]:
    """(row, {column: new value}) for every row the normalizer would change."""
    changes = []
    for row in rows:
        try:
            deal = _deal_of(row)
        except Exception as exc:  # an unreadable row is reported, never rewritten
            print(f"  ! row {row.get('id')}: unreadable ({exc}); skipped")
            continue
        params = storage._parse_params(row.get("search_params"))
        cleaned, _ = normalize_deal(deal, params, _found_on(row.get("found_at")))
        diff = {}
        for col in REPAIR_COLUMNS:
            new = getattr(cleaned, col)
            if col == "quality_flags":
                old_flags = storage._parse_flags(row.get(col))
                if sorted(new) != sorted(old_flags):
                    diff[col] = json.dumps(new) if new else ""
                continue
            # A column this DB doesn't have yet reads as None; the migration adds it as ''.
            old = row.get(col)
            if new != ("" if old is None else old):
                diff[col] = new
        if diff:
            changes.append((row, diff))
    return changes


def summarize(rows: list[dict], changes: list[tuple[dict, dict]]) -> str:
    per_column = Counter(col for _, diff in changes for col in diff)
    flags = Counter()
    for row, diff in changes:
        flags.update(json.loads(diff["quality_flags"] or "[]") if "quality_flags" in diff
                     else storage._parse_flags(row.get("quality_flags")))
    lines = [f"{len(rows)} rows scanned, {len(changes)} would change."]
    if per_column:
        lines.append("Changes per field:")
        lines += [f"  {col:<22} {n}" for col, n in per_column.most_common()]
    if flags:
        lines.append("Quality flags on changed rows:")
        lines += [f"  {flag:<28} {n}" for flag, n in flags.most_common()]
    if changes:
        lines.append(f"Samples (up to {SAMPLES}):")
        # Spread the samples over the change types rather than the first N ids.
        picked, seen = [], set()
        for row, diff in changes:
            key = frozenset(diff)
            if key not in seen:
                seen.add(key)
                picked.append((row, diff))
        for item in changes:
            if len(picked) >= SAMPLES:
                break
            if item not in picked:
                picked.append(item)
        for row, diff in picked[:SAMPLES]:
            lines.append(f"  #{row['id']} ({str(row.get('found_at') or '')[:10]}) {str(row.get('search_query') or '')[:60]}")
            for col, new in diff.items():
                lines.append(f"      {col}: {row.get(col)!r} -> {new!r}")
    return "\n".join(lines)


def apply(conn, changes: list[tuple[dict, dict]]) -> None:
    for row, diff in changes:
        cols = list(diff)
        conn.execute(f"UPDATE deals SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                     (*[diff[c] for c in cols], row["id"]))
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--db", help="SQLite file to repair (ignores TURSO_DATABASE_URL)")
    args = parser.parse_args(argv)

    if args.db:
        os.environ.pop("TURSO_DATABASE_URL", None)
        storage.DB_PATH = Path(args.db)
    turso = os.environ.get("TURSO_DATABASE_URL")
    print(f"Target: {'Turso ' + turso if turso else 'SQLite ' + str(storage.DB_PATH)}")
    print("Mode:   " + ("APPLY (writing)" if args.apply else "dry run (no writes; pass --apply to write)"))

    conn = storage._new_conn()
    try:
        if args.apply:
            storage._init_schema(conn)  # adds quality_flags if this DB predates it
        rows = storage._rows_as_dicts(conn.execute("SELECT * FROM deals ORDER BY id"))
        changes = plan(rows)
        print(summarize(rows, changes))
        if args.apply and changes:
            apply(conn, changes)
            print(f"Applied: {len(changes)} rows updated.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
