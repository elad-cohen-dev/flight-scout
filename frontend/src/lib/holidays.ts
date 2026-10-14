import type { HolidayWindow } from "../types";
import type { SearchForm } from "./search";

/** Holiday quick-picks (backend app/holidays.py): a school vacation fills the
 *  search form's date window, and its name becomes the dates text. */

const fmt = (iso: string, opts: Intl.DateTimeFormatOptions) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", opts);

/** "12 Dec". */
export function holidayDay(iso: string): string {
  return fmt(iso, { day: "numeric", month: "short" });
}

/** "5–12 Dec" · "25 Dec – 1 Jan" · "1 Jul – 31 Aug" · "12 Dec" (one day). */
export function holidayRange(w: Pick<HolidayWindow, "start" | "end">): string {
  if (w.start === w.end) return holidayDay(w.start);
  const sameMonth = w.start.slice(0, 7) === w.end.slice(0, 7);
  if (sameMonth) return `${Number(w.start.slice(8))}–${fmt(w.end, { day: "numeric", month: "short" })}`;
  return `${fmt(w.start, { day: "numeric", month: "short" })} – ${fmt(w.end, { day: "numeric", month: "short" })}`;
}

/** The form fields a holiday sets: an exact departure window (start to end -
 *  min nights), the trip length, and its id + name. One-way trips keep no nights. */
export function holidayFormPatch(w: HolidayWindow, roundTrip = true): Partial<SearchForm> {
  return {
    date_mode: "exact",
    depart_from: w.depart_from,
    depart_to: w.depart_to,
    nights_min: roundTrip ? String(w.nights_min) : "",
    nights_max: roundTrip ? String(w.nights_max) : "",
    holiday: w.id,
    dates_hint: w.name,
  };
}

/** The holiday the form is set to, while its window is still the holiday's. */
export function activeHoliday(form: Pick<SearchForm, "holiday" | "date_mode" | "depart_from">, windows: HolidayWindow[]): HolidayWindow | null {
  if (!form.holiday || form.date_mode !== "exact") return null;
  return windows.find((w) => w.id === form.holiday) ?? null;
}

const NAMES: Record<string, string> = {
  "rosh-hashana": "Rosh Hashana",
  "yom-kippur": "Yom Kippur",
  sukkot: "Sukkot",
  hanukkah: "Hanukkah",
  passover: "Passover",
  shavuot: "Shavuot",
  "independence-day": "Independence Day",
  summer: "Summer vacation",
};

/** "hanukkah-2026" -> "Hanukkah 2026" (History rows carry only the id). */
export function holidayName(id: string | null | undefined): string {
  const m = (id ?? "").trim().toLowerCase().match(/^(.+)-(\d{4})$/);
  if (!m) return "";
  const name = NAMES[m[1]] ?? m[1].replace(/-/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  return `${name} ${m[2]}`;
}

/** Quick-pick chips: the next few windows, soonest first. */
export function pickable(windows: HolidayWindow[], limit = 6): HolidayWindow[] {
  return windows.slice(0, limit);
}
