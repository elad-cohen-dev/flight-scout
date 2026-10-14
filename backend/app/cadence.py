"""Adaptive watches: each watch's credit plan — how often its live check runs.

The daily sweep still visits every watch every day; the plan decides whether
today's visit may spend live searches (SerpApi credits) or skips them:

- **Cadence by days to departure** (of the watch's latest best fare, else the
  middle of its window): more than FAR_DAYS out a live check weekly; NEAR_DAYS
  to FAR_DAYS every 3 days; inside NEAR_DAYS daily — that's when short-haul
  prices move.
- **Volatility** of the watch's recent run bests: flat prices (range under
  FLAT_PCT over 3+ runs) -> twice the interval (up to MAX_INTERVAL); a big
  move (a step of MOVE_PCT, or a range of 1.5x that) -> half of it.
- **Budget**: every watch has a monthly live-search budget — `credit_budget`,
  else a fair share of WATCH_POOL_SHARE of SERPAPI_MONTHLY_BUDGET split across
  the active watches that spend credits (explicit budgets first). The interval
  stretches so the checks left this month fit what is left of it; with less
  than one check left the watch waits for next month.

The free pre-screen (engine.prescreen_decision) still runs on the cache path:
a fare close to the threshold or a smart rule about to fire may override a
"not today", as long as the watch's budget has a check left. A manual
"Run now" ignores the plan (and its credits count). Pure — callers pass the
numbers and "today".
"""

from __future__ import annotations

import math
import statistics
from calendar import monthrange
from datetime import date, timedelta

from .models import WatchPlan

FAR_DAYS = 90
NEAR_DAYS = 30
BASE_INTERVAL = {"far": 7, "mid": 3, "near": 1}
MAX_INTERVAL = 14
FLAT_PCT = 0.03          # run bests within 3% of each other = flat
MOVE_PCT = 0.10          # one step of 10% (or a 15% range) = a big move
VOLATILITY_RUNS = 5      # the last N run bests decide
WATCH_POOL_SHARE = 0.7   # watches share 70% of the monthly budget; the rest is for searches & details


def base_interval(days_out: int | None) -> tuple[int, str]:
    """(days between live checks, why) by days to departure."""
    if days_out is None:
        return BASE_INTERVAL["mid"], "departure date unknown"
    if days_out > FAR_DAYS:
        return BASE_INTERVAL["far"], f"{days_out} days out"
    if days_out >= NEAR_DAYS:
        return BASE_INTERVAL["mid"], f"{days_out} days out"
    return BASE_INTERVAL["near"], f"{max(days_out, 0)} days out"


def volatility(prices: list[float]) -> str:
    """'flat' | 'moving' | '' (too few runs, or ordinary moves) over the last
    VOLATILITY_RUNS run bests, oldest first."""
    last = [p for p in prices if p and p > 0][-VOLATILITY_RUNS:]
    if len(last) < 2:
        return ""
    mid = statistics.median(last)
    spread = (max(last) - min(last)) / mid
    step = max(abs(b - a) / a for a, b in zip(last, last[1:]))
    if step >= MOVE_PCT or spread >= MOVE_PCT * 1.5:
        return "moving"
    if len(last) >= 3 and spread < FLAT_PCT:
        return "flat"
    return ""


def interval_for(days_out: int | None, prices: list[float]) -> tuple[int, str]:
    """The cadence before the budget: (days, reason)."""
    days, why = base_interval(days_out)
    vol = volatility(prices)
    if vol == "flat":
        days, why = min(MAX_INTERVAL, days * 2), why + ", prices steady"
    elif vol == "moving":
        days, why = max(1, days // 2), why + ", prices moving"
    return days, why


def fair_shares(spenders: dict[int, int | None], budget: int) -> dict[int, int]:
    """Monthly budget per watch: {id: credit_budget or None} of the active
    watches that spend credits -> {id: budget}. Explicit budgets are kept;
    the rest of the watch pool is split evenly among the others."""
    pool = int(budget * WATCH_POOL_SHARE)
    explicit = {i: b for i, b in spenders.items() if b is not None}
    rest = [i for i, b in spenders.items() if b is None]
    left = max(0, pool - sum(explicit.values()))
    share = left // len(rest) if rest else 0
    return {**explicit, **{i: share for i in rest}}


def _days_left_in_month(today: date) -> int:
    return monthrange(today.year, today.month)[1] - today.day + 1


def _next_month(today: date) -> date:
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


def _in_days(n: int) -> str:
    return "today" if n <= 0 else "tomorrow" if n == 1 else f"in {n} days"


def make_plan(per_check: int, days_out: int | None, prices: list[float], used: int, budget: int,
              last_check: date | None, today: date, budget_default: bool = True) -> WatchPlan:
    """A watch's plan for today. `per_check` = live searches one check spends
    (engine.watch_credits), `used` = spent this month, `last_check` = the day
    it last spent any. per_check 0 (cached / web only) -> no live checks."""
    if per_check <= 0:
        return WatchPlan(per_check=0, interval_days=0, used=used, budget=budget, budget_default=budget_default,
                         reason="no live searches for this watch", text="")
    interval, reason = interval_for(days_out, prices)
    left = budget - used
    days_left = _days_left_in_month(today)
    credits = f"{used}/{budget} credits this month"
    if left < per_check:
        nxt = _next_month(today)
        if last_check and last_check + timedelta(days=interval) > nxt:
            nxt = last_check + timedelta(days=interval)
        n = (nxt - today).days
        return WatchPlan(per_check=per_check, interval_days=interval, next_check=nxt, next_in_days=n,
                         used=used, budget=budget, budget_default=budget_default, per_month=used,
                         reason=f"monthly budget used ({credits})",
                         text=f"next live check: {_in_days(n)} (budget used) · {credits}")
    checks_left = left // per_check
    fit = math.ceil(days_left / checks_left)
    if fit > interval:
        interval, reason = fit, reason + f"; every {fit} days to fit the budget"
    nxt = max(today, last_check + timedelta(days=interval)) if last_check else today
    n = (nxt - today).days
    # This month's projection: spent so far plus the checks still to come.
    to_come = 0 if n >= days_left else 1 + (days_left - 1 - n) // interval
    per_month = min(budget, used + to_come * per_check)
    every = "daily" if interval == 1 else f"every {interval} days"
    return WatchPlan(per_check=per_check, interval_days=interval, next_check=nxt, next_in_days=n,
                     used=used, budget=budget, budget_default=budget_default, per_month=per_month,
                     reason=f"{reason}: {every}", text=f"next live check: {_in_days(n)} · {credits}")


def due(plan: WatchPlan | None, today: date) -> bool:
    """May today's run spend live searches? No plan = yes (the old behaviour)."""
    if plan is None or plan.per_check <= 0:
        return True
    return plan.next_check is not None and plan.next_check <= today and plan.budget - plan.used >= plan.per_check


def has_budget(plan: WatchPlan | None) -> bool:
    return plan is None or plan.per_check <= 0 or plan.budget - plan.used >= plan.per_check
