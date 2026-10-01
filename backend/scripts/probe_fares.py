"""Probe the real fare APIs with a handful of queries and compare them side by side.

    cd backend && uv run python scripts/probe_fares.py          # plan only: queries + credits it would spend
    cd backend && uv run python scripts/probe_fares.py --yes    # run them

Needs SERPAPI_API_KEY and/or TRAVELPAYOUTS_TOKEN (environment or backend/.env);
refuses to run with neither. Queries: TLV -> PRG, BUD, LTN, ATH, FCO, round trip
next month (a 4-night trip from the 10th), 1 adult, plus TLV -> PRG for 2 adults
— that pair checks that SerpApi prices are party totals (expect ~2x).

SerpApi: at most 6 searches, minus any still in the 1h response cache; they
count against SERPAPI_MONTHLY_BUDGET like app searches. Travelpayouts is free:
one exact-dates call and one month-calendar call per query. Afterwards it reads
SerpApi's free Account API for the plan's real remaining quota.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storage  # noqa: E402  (loads backend/.env)
from app.models import Passengers, SearchParams  # noqa: E402
from app.providers import base, serpapi, travelpayouts  # noqa: E402
from app.providers.base import FareQuery, ProviderError  # noqa: E402

DESTINATIONS = ["PRG", "BUD", "LTN", "ATH", "FCO"]
MAX_SERPAPI_CREDITS = 6


def planned_queries(today: date) -> list[FareQuery]:
    first = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    depart = first + timedelta(days=9)
    back = depart + timedelta(days=4)
    one = SearchParams(currency="USD")
    two = SearchParams(currency="USD", passengers=Passengers(adults=2))
    queries = [FareQuery("TLV", d, depart.isoformat(), back.isoformat(), one) for d in DESTINATIONS]
    queries.append(FareQuery("TLV", "PRG", depart.isoformat(), back.isoformat(), two))
    return queries


def _serp_cached(q: FareQuery) -> bool:
    try:
        return storage.cache_get(base.cache_key(serpapi.PROVIDER, serpapi.request_params(q))) is not None
    except Exception:
        return False


def _label(q: FareQuery) -> str:
    return f"{q.origin}->{q.destination} {q.depart_date}/{q.return_date} x{q.params.passengers.adults}ad"


def _best(deals) -> str:
    if not deals:
        return "—"
    d = min(deals, key=lambda x: x.price)
    per = f" ({d.price_per_person:,.0f}/pp)" if d.price_per_person else ""
    return f"{d.price:,.0f} {d.currency}{per} {d.airline_code or d.airline} {d.flight_numbers}".strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually call the APIs")
    args = ap.parse_args()

    use_serp, use_tp = serpapi.configured(), travelpayouts.configured()
    if not use_serp and not use_tp:
        print("Refusing to run: set SERPAPI_API_KEY and/or TRAVELPAYOUTS_TOKEN (env or backend/.env).")
        return 2

    queries = planned_queries(date.today())
    credits = sum(1 for q in queries if not _serp_cached(q)) if use_serp else 0
    assert credits <= MAX_SERPAPI_CREDITS
    print("Queries:")
    for q in queries:
        print(f"  {_label(q)}")
    if use_serp:
        print(f"SerpApi: will use {credits} search credit(s) "
              f"({len(queries) - credits} cached); budget left this month: {serpapi.remaining()} "
              f"of {serpapi.monthly_budget()}.")
    else:
        print("SerpApi: SERPAPI_API_KEY not set — skipped.")
    print("Travelpayouts: " + (f"{2 * len(queries)} free calls (cached 6h)." if use_tp
                               else "TRAVELPAYOUTS_TOKEN not set — skipped."))
    if use_tp and not travelpayouts.marker():
        print("  note: TRAVELPAYOUTS_MARKER not set — Aviasales links won't carry your marker.")
    if not args.yes:
        print("\nDry run. Re-run with --yes to call the APIs.")
        return 0
    if use_serp and credits > serpapi.remaining():
        print("Not enough SerpApi budget left this month; aborting.")
        return 1

    rows = []
    insights = {}
    for q in queries:
        live = cached = calendar = "skipped"
        if use_serp:
            try:
                r = serpapi.search(q)
                live = _best(r.deals) + (" [cache]" if r.cached else "")
                if r.insights:
                    i = r.insights
                    insights[_label(q)] = f"{i.price_level or '?'}, typical {i.typical_low}-{i.typical_high} {i.currency}"
            except ProviderError as exc:
                live = f"error: {exc}"
        if use_tp:
            try:
                cached = _best(travelpayouts.search(q).deals)
                month = FareQuery(q.origin, q.destination, q.depart_date[:7], "", q.params)
                cal = travelpayouts.search(month).deals
                calendar = f"{len(cal)} fares, cheapest {_best(cal)}"
            except ProviderError as exc:
                cached = calendar = f"error: {exc}"
        rows.append((_label(q), live, cached, calendar))

    print()
    for label, live, cached, calendar in rows:
        print(label)
        print(f"  Google Flights (live): {live}")
        print(f"  Aviasales (exact):     {cached}")
        print(f"  Aviasales (month):     {calendar}")
        if label in insights:
            print(f"  Google price insights: {insights[label]}")
    if use_serp:
        try:
            account = base.http_get_json("https://serpapi.com/account.json", {"api_key": serpapi.api_key()},
                                         provider="serpapi")
            print(f"\nSerpApi account: {account.get('this_month_usage')} used this month, "
                  f"{account.get('plan_searches_left')} left on the plan; our counter: {serpapi.used_this_month()}.")
        except ProviderError as exc:
            print(f"\nSerpApi account lookup failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
