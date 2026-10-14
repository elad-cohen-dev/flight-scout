import { useEffect, useState } from "react";
import { fetchHolidays } from "../api";
import { activeHoliday, holidayDay, holidayFormPatch, holidayRange, pickable } from "../lib/holidays";
import type { SearchForm } from "../lib/search";
import type { HolidayWindow } from "../types";
import Icon from "./Icon";
import "./advice.css";

// One fetch per page load: the calendar only changes with a deploy.
let cache: Promise<HolidayWindow[]> | null = null;
function loadHolidays(): Promise<HolidayWindow[]> {
  cache ??= fetchHolidays().catch(() => {
    cache = null; // retry on the next mount
    return [];
  });
  return cache;
}

/** "Holiday" quick-picks: an Israeli school vacation fills the date window
 *  (departures, trip length) and names the search "Hanukkah 2026". The
 *  backend keeps the trip inside the vacation and finds its cheapest week. */
export default function HolidayPicks({ form, onChange }: { form: SearchForm; onChange: (patch: Partial<SearchForm>) => void }) {
  const [windows, setWindows] = useState<HolidayWindow[]>([]);
  useEffect(() => {
    let alive = true;
    loadHolidays().then((w) => alive && setWindows(w));
    return () => {
      alive = false;
    };
  }, []);
  if (windows.length === 0) return null;
  const active = activeHoliday(form, windows);
  const roundTrip = form.trip_type === "round_trip";
  return (
    <>
      <div className="holiday-picks" role="group" aria-label="School holidays">
        <span className="field-label">Holiday</span>
        {pickable(windows).map((w) => {
          const on = active?.id === w.id;
          return (
            <button
              key={w.id}
              type="button"
              className={`chip holiday-chip${on ? " chip-active" : ""}`}
              aria-pressed={on}
              title={`No school ${holidayRange(w)}${w.confirmed ? "" : " (predicted — the Ministry calendar isn't out yet)"}. Finds the cheapest ${w.nights_min}–${w.nights_max} night trip inside it.`}
              onClick={() => onChange(on ? { holiday: "", date_mode: "flexible" } : holidayFormPatch(w, roundTrip))}
            >
              {w.name}
              <small>{holidayRange(w)}</small>
            </button>
          );
        })}
      </div>
      {active && (
        <p className="holiday-note small">
          <Icon name="calendar" size={13} /> Out from {holidayRange({ start: active.depart_from, end: active.depart_to })}, back by {holidayDay(active.end)} · cheapest week inside the vacation
          {active.note ? ` · ${active.note}` : ""}
        </p>
      )}
    </>
  );
}
