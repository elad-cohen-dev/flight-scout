// Trip guides (3.1): a private, per-trip document — the day-by-day schedule,
// places worth going to, bookings and practical tips.
//
// The document is personal (dates away from home, hotel, booking references),
// so it is never part of this repo or the built app: it is imported from a JSON
// file in the Guide tab and stored on the server behind the app password
// (backend /api/guides). This module owns its shape: `normalizeGuide` turns
// whatever was imported or fetched into a `TripGuide` the UI can trust.

export const PLACE_KINDS = ["coffee", "food", "shop", "sight", "market", "festive", "show", "stay", "transport"] as const;
export type PlaceKind = (typeof PLACE_KINDS)[number];

export const STOP_KINDS = [...PLACE_KINDS, "flight", "walk", "rest", "note"] as const;
export type StopKind = (typeof STOP_KINDS)[number];

export const BOOKING_KINDS = ["flight", "stay", "show", "tour", "other"] as const;
export type BookingKind = (typeof BOOKING_KINDS)[number];

export const KIND_LABELS: Record<PlaceKind, string> = {
  coffee: "Coffee",
  food: "Food",
  shop: "Shops",
  sight: "Sights",
  market: "Markets",
  festive: "Festive",
  show: "Shows",
  stay: "Stay",
  transport: "Transport",
};

export interface GuidePlace {
  id: string;
  name: string;
  kind: PlaceKind;
  /** Neighbourhood; places are grouped by it. */
  area: string;
  address: string;
  hours: string;
  /** Why go / what to order. */
  note: string;
  tags: string[];
  /** Official site (http/https only), or "". */
  url: string;
  /** What to search for in a maps app; defaults to "name, address". */
  map: string;
  /** A top pick: starred and listed first in its area. */
  pick: boolean;
}

export interface GuideStop {
  /** "09:30", or loose: "Morning". */
  time: string;
  title: string;
  kind: StopKind;
  /** Id of a place in `places`, or "". */
  place: string;
  note: string;
  /** How to get here from the previous stop. */
  travel: string;
  /** Booked for this time — the rest of the day moves around it. */
  fixed: boolean;
}

export interface GuideDay {
  /** ISO date. */
  date: string;
  title: string;
  summary: string;
  stops: GuideStop[];
  /** "If there's time" ideas. */
  extras: string[];
}

export interface GuideBooking {
  kind: BookingKind;
  title: string;
  /** As shown: "Tue 24 Nov · 19:30". */
  when: string;
  /** Local start, "2026-11-24T19:30" — orders bookings and finds the next one. */
  at: string;
  lines: string[];
  /** Confirmation number. */
  ref: string;
  /** Id of a place in `places`, or "". */
  place: string;
}

export interface GuideTodo {
  id: string;
  text: string;
  note: string;
  /** As shown: "by Fri 20 Nov". */
  due: string;
}

export interface GuideTip {
  title: string;
  body: string;
}

export interface GuideTipGroup {
  title: string;
  /** A PlaceKind / StopKind name picks the icon. */
  kind: StopKind;
  tips: GuideTip[];
}

export interface TripGuide {
  version: 1;
  slug: string;
  /** Short: it is also the tab's label. */
  title: string;
  subtitle: string;
  /** ISO dates, first and last day. */
  start: string;
  end: string;
  /** "Facts checked 19 Nov 2026" — shown so stale hours are obvious. */
  checked: string;
  bookings: GuideBooking[];
  todos: GuideTodo[];
  days: GuideDay[];
  places: GuidePlace[];
  tips: GuideTipGroup[];
}

/** A stored guide as the API returns it. */
export interface StoredGuide {
  slug: string;
  updated_at: string;
  data: unknown;
}

export class GuideError extends Error {}

// --- reading a document ----------------------------------------------------------------

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v.trim() : typeof v === "number" ? String(v) : "");
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const strings = (v: unknown): string[] => list(v).map(str).filter(Boolean);
const oneOf = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T =>
  (allowed as readonly string[]).includes(str(v)) ? (str(v) as T) : fallback;

function isDate(v: string): boolean {
  if (!ISO_DATE.test(v)) return false;
  const t = Date.parse(`${v}T00:00:00Z`);
  // a real calendar day: "2027-02-30" parses, but as 2 March
  return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === v;
}

/** Only web links survive: a guide file must not be able to smuggle in `javascript:`. */
export function safeUrl(v: unknown): string {
  const s = str(v);
  if (!/^https?:\/\//i.test(s)) return "";
  try {
    return new URL(s).href;
  } catch {
    return "";
  }
}

export function slugify(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "") // accents: "é" -> "e"
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/, "");
}

function place(raw: unknown, index: number): GuidePlace | null {
  if (!isObject(raw)) return null;
  const name = str(raw.name);
  if (!name) return null;
  const address = str(raw.address);
  return {
    id: str(raw.id) || slugify(name) || `place-${index}`,
    name,
    kind: oneOf(raw.kind, PLACE_KINDS, "sight"),
    area: str(raw.area) || "Elsewhere",
    address,
    hours: str(raw.hours),
    note: str(raw.note),
    tags: strings(raw.tags),
    url: safeUrl(raw.url),
    map: str(raw.map) || [name, address].filter(Boolean).join(", "),
    pick: raw.pick === true,
  };
}

function stop(raw: unknown, placeIds: Set<string>): GuideStop | null {
  if (!isObject(raw)) return null;
  const title = str(raw.title);
  if (!title) return null;
  const ref = str(raw.place);
  return {
    time: str(raw.time),
    title,
    kind: oneOf(raw.kind, STOP_KINDS, "note"),
    place: placeIds.has(ref) ? ref : "",
    note: str(raw.note),
    travel: str(raw.travel),
    fixed: raw.fixed === true,
  };
}

function day(raw: unknown, placeIds: Set<string>): GuideDay | null {
  if (!isObject(raw)) return null;
  const date = str(raw.date);
  if (!isDate(date)) return null;
  return {
    date,
    title: str(raw.title),
    summary: str(raw.summary),
    stops: list(raw.stops)
      .map((s) => stop(s, placeIds))
      .filter((s): s is GuideStop => s !== null),
    extras: strings(raw.extras),
  };
}

function booking(raw: unknown, placeIds: Set<string>): GuideBooking | null {
  if (!isObject(raw)) return null;
  const title = str(raw.title);
  if (!title) return null;
  const at = str(raw.at);
  const ref = str(raw.place);
  return {
    kind: oneOf(raw.kind, BOOKING_KINDS, "other"),
    title,
    when: str(raw.when),
    at: Number.isNaN(Date.parse(at)) ? "" : at,
    lines: strings(raw.lines),
    ref: str(raw.ref),
    place: placeIds.has(ref) ? ref : "",
  };
}

/** Validate an imported or fetched document. Throws a GuideError a person can
 *  act on when the core is missing; anything merely malformed further down
 *  (an unnamed place, a stop without a title) is dropped instead. */
export function normalizeGuide(raw: unknown, fallbackSlug = ""): TripGuide {
  if (!isObject(raw)) throw new GuideError("That file isn't a guide: expected a JSON object.");
  const title = str(raw.title);
  if (!title) throw new GuideError("The guide has no title.");
  const start = str(raw.start);
  const end = str(raw.end);
  if (!isDate(start) || !isDate(end)) throw new GuideError("The guide needs start and end dates like 2026-11-21.");
  if (end < start) throw new GuideError("The guide ends before it starts.");

  const seen = new Set<string>();
  const places = list(raw.places)
    .map(place)
    .filter((p): p is GuidePlace => {
      if (!p || seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    });
  const days = list(raw.days)
    .map((d) => day(d, seen))
    .filter((d): d is GuideDay => d !== null)
    .sort((a, b) => a.date.localeCompare(b.date));
  if (!days.length && !places.length) throw new GuideError("The guide has no days and no places.");

  const slug = [str(raw.slug), fallbackSlug, slugify(`${title}-${start}`)].find((s) => SLUG.test(s));
  if (!slug) throw new GuideError("The guide needs a slug: lowercase letters, digits and dashes.");

  return {
    version: 1,
    slug,
    title,
    subtitle: str(raw.subtitle),
    start,
    end,
    checked: str(raw.checked),
    bookings: list(raw.bookings)
      .map((b) => booking(b, seen))
      .filter((b): b is GuideBooking => b !== null)
      .sort((a, b) => (a.at || "9999").localeCompare(b.at || "9999")),
    todos: list(raw.todos)
      .map((t, i): GuideTodo | null => {
        if (!isObject(t) || !str(t.text)) return null;
        return { id: str(t.id) || `todo-${i}`, text: str(t.text), note: str(t.note), due: str(t.due) };
      })
      .filter((t): t is GuideTodo => t !== null),
    days,
    places,
    tips: list(raw.tips)
      .map((g): GuideTipGroup | null => {
        if (!isObject(g) || !str(g.title)) return null;
        const tips = list(g.tips)
          .map((t): GuideTip | null => (isObject(t) && (str(t.title) || str(t.body)) ? { title: str(t.title), body: str(t.body) } : null))
          .filter((t): t is GuideTip => t !== null);
        return tips.length ? { title: str(g.title), kind: oneOf(g.kind, STOP_KINDS, "note"), tips } : null;
      })
      .filter((g): g is GuideTipGroup => g !== null),
  };
}

/** The stored guides that are readable, each normalized. */
export function readGuides(stored: StoredGuide[]): TripGuide[] {
  const out: TripGuide[] = [];
  for (const s of stored) {
    try {
      out.push(normalizeGuide(s.data, s.slug));
    } catch {
      // an unreadable document is skipped, not fatal: the others still show
    }
  }
  return out;
}

// --- dates -------------------------------------------------------------------------------

/** The device's calendar date — on the trip, the phone is on local time. */
export function todayISO(now = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/** The device's wall-clock time as "2026-11-24T19:30", comparable with a booking's `at`. */
export function nowLocal(now = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${todayISO(now)}T${p(now.getHours())}:${p(now.getMinutes())}`;
}

function utc(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`);
}

export function daysBetween(from: string, to: string): number {
  return Math.round((utc(to) - utc(from)) / 86_400_000);
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function dayParts(iso: string): { weekday: string; day: number; month: string } {
  const d = new Date(utc(iso));
  return { weekday: WEEKDAYS[d.getUTCDay()], day: d.getUTCDate(), month: MONTHS[d.getUTCMonth()] };
}

/** "Tue 24 Nov" */
export function dayLabel(iso: string): string {
  const p = dayParts(iso);
  return `${p.weekday} ${p.day} ${p.month}`;
}

/** "21–26 Nov 2026", "28 Dec 2026 – 2 Jan 2027" */
export function dateRange(start: string, end: string): string {
  const a = dayParts(start);
  const b = dayParts(end);
  const ya = start.slice(0, 4);
  const yb = end.slice(0, 4);
  if (start === end) return `${a.day} ${a.month} ${ya}`;
  if (ya !== yb) return `${a.day} ${a.month} ${ya} – ${b.day} ${b.month} ${yb}`;
  if (a.month !== b.month) return `${a.day} ${a.month} – ${b.day} ${b.month} ${yb}`;
  return `${a.day}–${b.day} ${b.month} ${yb}`;
}

export type TripPhase =
  | { phase: "before"; daysToGo: number }
  | { phase: "during"; dayNumber: number; totalDays: number }
  | { phase: "after"; daysAgo: number };

export function tripPhase(guide: Pick<TripGuide, "start" | "end">, today: string): TripPhase {
  if (today < guide.start) return { phase: "before", daysToGo: daysBetween(today, guide.start) };
  if (today > guide.end) return { phase: "after", daysAgo: daysBetween(guide.end, today) };
  return { phase: "during", dayNumber: daysBetween(guide.start, today) + 1, totalDays: daysBetween(guide.start, guide.end) + 1 };
}

export function phaseLabel(p: TripPhase): string {
  if (p.phase === "before") return p.daysToGo === 1 ? "Tomorrow" : `In ${p.daysToGo} days`;
  if (p.phase === "during") return `Day ${p.dayNumber} of ${p.totalDays}`;
  return p.daysAgo === 1 ? "Ended yesterday" : `Ended ${p.daysAgo} days ago`;
}

/** Which day the plan opens on: today while travelling, otherwise the first. */
export function defaultDayIndex(guide: Pick<TripGuide, "days">, today: string): number {
  const i = guide.days.findIndex((d) => d.date === today);
  return i >= 0 ? i : 0;
}

/** The trip to show: one under way, else the next to start, else the latest past one. */
export function pickGuide(guides: TripGuide[], today: string): TripGuide | null {
  if (!guides.length) return null;
  const current = guides.filter((g) => g.start <= today && today <= g.end);
  if (current.length) return current.sort((a, b) => a.start.localeCompare(b.start))[0];
  const upcoming = guides.filter((g) => g.start > today).sort((a, b) => a.start.localeCompare(b.start));
  if (upcoming.length) return upcoming[0];
  return [...guides].sort((a, b) => b.end.localeCompare(a.end))[0];
}

/** The next booking that hasn't started (flights, shows, check-in). */
export function nextBooking(guide: Pick<TripGuide, "bookings">, now: string): GuideBooking | null {
  return guide.bookings.find((b) => b.at && b.at >= now) ?? null;
}

// --- places --------------------------------------------------------------------------------

/** Kinds the guide actually has places for, in the canonical order. */
export function placeKinds(places: GuidePlace[]): PlaceKind[] {
  return PLACE_KINDS.filter((k) => places.some((p) => p.kind === k));
}

/** Places grouped by area — areas in first-seen order, top picks first within each. */
export function groupByArea(places: GuidePlace[]): { area: string; places: GuidePlace[] }[] {
  const groups = new Map<string, GuidePlace[]>();
  for (const p of places) groups.set(p.area, [...(groups.get(p.area) ?? []), p]);
  return [...groups].map(([area, ps]) => ({ area, places: [...ps].sort((a, b) => Number(b.pick) - Number(a.pick)) }));
}

/** Place id -> the days whose plan stops there ("Tue 24 Nov"). */
export function plannedDays(guide: Pick<TripGuide, "days">): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const d of guide.days) {
    for (const s of d.stops) {
      if (!s.place) continue;
      const label = dayParts(d.date).weekday;
      const days = out.get(s.place) ?? [];
      if (!days.includes(label)) out.set(s.place, [...days, label]);
    }
  }
  return out;
}

/** Opens the place in whichever maps app the phone prefers. */
export function mapsUrl(p: Pick<GuidePlace, "map">): string {
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.map)}`;
}

/** Public-transport directions from wherever you are right now. */
export function directionsUrl(p: Pick<GuidePlace, "map">): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(p.map)}&travelmode=transit`;
}

// --- on-device state -----------------------------------------------------------------------
//
// The last fetched guides are kept on the device so the tab opens instantly —
// underground, or while the free server is still waking up. It is the same
// private data the password protects, so locking the app wipes it.

const CACHE_KEY = "fs_guides_v1";
const TICKS_KEY = "fs_guide_ticks_v1";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // private mode / full storage: the tab just loads from the network each time
  }
}

export function loadCachedGuides(): TripGuide[] {
  const cached = read<unknown>(CACHE_KEY, []);
  return readGuides(list(cached).map((data) => ({ slug: "", updated_at: "", data })));
}

export function saveCachedGuides(guides: TripGuide[]): void {
  write(CACHE_KEY, guides);
}

export function clearCachedGuides(): void {
  try {
    localStorage.removeItem(CACHE_KEY);
    localStorage.removeItem(TICKS_KEY);
  } catch {
    // nothing stored
  }
}

/** The tab's label: the cached trip's title ("London"), so the nav names the trip. */
export function cachedGuideTitle(today = todayISO()): string {
  return pickGuide(loadCachedGuides(), today)?.title ?? "";
}

/** Ticked to-dos of one guide (per device). */
export function loadTicks(slug: string): string[] {
  return strings(read<Record<string, unknown>>(TICKS_KEY, {})[slug]);
}

export function saveTicks(slug: string, ids: string[]): void {
  write(TICKS_KEY, { ...read<Record<string, unknown>>(TICKS_KEY, {}), [slug]: ids });
}

// --- the tab's sub-views, mirrored in the hash ("#guide/places") ------------------------------

export const GUIDE_VIEWS = ["plan", "places", "around", "bookings"] as const;
export type GuideView = (typeof GUIDE_VIEWS)[number];

export function viewFromHash(hash: string): GuideView {
  const m = hash.match(/^#\/?guide\/([a-z]+)/);
  return m && (GUIDE_VIEWS as readonly string[]).includes(m[1]) ? (m[1] as GuideView) : "plan";
}
