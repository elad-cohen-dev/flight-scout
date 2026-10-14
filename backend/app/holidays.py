"""Holiday windows: Israeli school vacations and Jewish/Israeli holidays.

The calendar lives in data/holidays.json (sources and how each date was checked
are in that file): school years 2026-27 (the Ministry of Education calendar)
and 2027-28 (predicted until the Ministry publishes it — `confirmed: false`).

A window is the stretch with no school: a family can fly out on `start` and
must be back by `end`. A holiday search fills the ordinary date window from it
(engine.plan_dates): departures from `start` to `end - nights_min`, trip length
`nights`, and the return capped at `end`, so the flexible-date path (Google
Travel Explore -> the cheapest pair confirmed on Google Flights) finds the
cheapest week that fits the vacation. Pure apart from reading the file once.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent / "data" / "holidays.json"


class HolidayWindow(BaseModel):
    """One school vacation / holiday window (GET /api/holidays)."""

    id: str = Field(description="e.g. 'hanukkah-2026' — SearchParams.holiday")
    name: str = Field(description="e.g. 'Hanukkah 2026' — the search's dates_hint")
    kind: str = Field(description="rosh_hashana | yom_kippur | sukkot | hanukkah | passover | independence_day | "
                                  "shavuot | summer")
    school_year: str = Field(description="e.g. '2026-27'")
    start: date = Field(description="First day with no school (fly out)")
    end: date = Field(description="Last day with no school (fly back)")
    holiday_start: date | None = None
    holiday_end: date | None = None
    nights_min: int
    nights_max: int
    depart_from: date = Field(description="Search window: earliest departure (= start)")
    depart_to: date = Field(description="Search window: latest departure (end - nights_min)")
    confirmed: bool = Field(default=True, description="False = predicted, the Ministry calendar isn't out yet")
    note: str = ""
    sources: list[str] = Field(default_factory=list, description="Source URLs")


@lru_cache(maxsize=1)
def windows() -> tuple[HolidayWindow, ...]:
    """Every window in the data file, by start date. A bad entry is skipped
    (logged) rather than breaking searches."""
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("holiday calendar unreadable")
        return ()
    urls = data.get("sources", {})
    out = []
    for raw in data.get("windows", []):
        try:
            start, end = date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
            span = (end - start).days
            lo, hi = raw["nights"]
            lo, hi = min(lo, span), min(hi, span)
            out.append(HolidayWindow(
                id=raw["id"], name=raw["name"], kind=raw["kind"], school_year=raw["school_year"],
                start=start, end=end,
                holiday_start=raw.get("holiday_start"), holiday_end=raw.get("holiday_end"),
                nights_min=lo, nights_max=max(lo, hi),
                depart_from=start, depart_to=end - timedelta(days=lo),
                confirmed=raw.get("confirmed", True), note=raw.get("note", ""),
                sources=[urls.get(s, s) for s in raw.get("sources", [])],
            ))
        except Exception:
            logger.exception("holiday window %r skipped", raw.get("id"))
    return tuple(sorted(out, key=lambda w: (w.start, w.end)))


def get(holiday_id: str) -> HolidayWindow | None:
    key = (holiday_id or "").strip().lower()
    return next((w for w in windows() if w.id == key), None) if key else None


def upcoming(today: date, include_past: bool = False) -> list[HolidayWindow]:
    """Windows a trip can still fit: the last departure day not yet past."""
    return list(windows()) if include_past else [w for w in windows() if w.depart_to >= today]


def name_of(holiday_id: str) -> str:
    window = get(holiday_id)
    return window.name if window else ""
