import { describe, expect, it } from "vitest";
import {
  dateRange,
  dayLabel,
  daysBetween,
  defaultDayIndex,
  directionsUrl,
  groupByArea,
  GuideError,
  mapsUrl,
  nextBooking,
  normalizeGuide,
  nowLocal,
  phaseLabel,
  pickGuide,
  placeKinds,
  plannedDays,
  readGuides,
  safeUrl,
  slugify,
  todayISO,
  tripPhase,
  viewFromHash,
} from "./guide";
import type { TripGuide } from "./guide";

/** A small, made-up guide in the shape an imported file has. */
const raw = () => ({
  version: 1,
  slug: "rome-2027-03",
  title: "Rome",
  subtitle: "2 adults",
  start: "2027-03-05",
  end: "2027-03-08",
  checked: "Facts checked 1 Mar 2027",
  places: [
    { id: "eustachio", name: "Sant’Eustachio Il Caffè", kind: "coffee", area: "Pantheon", address: "Piazza di S. Eustachio 82", hours: "07:30–24:00", note: "Gran caffè at the bar.", tags: ["Standing"], url: "https://caffesanteustachio.com/", pick: true },
    { id: "tazza", name: "Tazza d’Oro", kind: "coffee", area: "Pantheon" },
    { id: "campo", name: "Campo de’ Fiori market", kind: "market", area: "Centro" },
    { id: "galleria", name: "Galleria Borghese", kind: "sight", area: "Villa Borghese", pick: true },
  ],
  days: [
    {
      date: "2027-03-06",
      title: "Centro",
      stops: [
        { time: "09:00", title: "Coffee at the bar", kind: "coffee", place: "eustachio" },
        { time: "17:00", title: "Galleria Borghese", kind: "sight", place: "galleria", fixed: true, travel: "Bus 116, 20 min" },
      ],
      extras: ["Giolitti for gelato"],
    },
    { date: "2027-03-05", title: "Arrive", stops: [{ time: "Evening", title: "Check in", kind: "stay" }] },
  ],
  bookings: [
    { kind: "show", title: "Galleria Borghese", when: "Sat 6 Mar · 17:00", at: "2027-03-06T17:00", ref: "GB-1", place: "galleria" },
    { kind: "flight", title: "TLV → FCO", when: "Fri 5 Mar · 06:10", at: "2027-03-05T06:10", lines: ["Terminal 3"] },
  ],
  todos: [{ id: "checkin", text: "Online check-in", due: "Thu 4 Mar" }, { text: "Book the Borghese slot" }],
  tips: [{ title: "Getting around", kind: "transport", tips: [{ title: "Tickets", body: "Tap a card on the bus.\nOne tap per rider." }] }],
});

const guide = (over: Record<string, unknown> = {}): TripGuide => normalizeGuide({ ...raw(), ...over });

describe("normalizeGuide", () => {
  it("reads a full document, sorting days and bookings by date", () => {
    const g = guide();
    expect(g.slug).toBe("rome-2027-03");
    expect(g.days.map((d) => d.date)).toEqual(["2027-03-05", "2027-03-06"]);
    expect(g.bookings.map((b) => b.title)).toEqual(["TLV → FCO", "Galleria Borghese"]);
    expect(g.places[0]).toMatchObject({ id: "eustachio", pick: true, map: "Sant’Eustachio Il Caffè, Piazza di S. Eustachio 82" });
    expect(g.places[1]).toMatchObject({ address: "", hours: "", tags: [], url: "", pick: false, map: "Tazza d’Oro" });
    expect(g.todos.map((t) => t.id)).toEqual(["checkin", "todo-1"]);
    expect(g.tips[0].tips[0].body).toContain("\n");
  });

  it("is idempotent, so the stored copy reads back the same", () => {
    const g = guide();
    expect(normalizeGuide(JSON.parse(JSON.stringify(g)))).toEqual(g);
  });

  it("rejects documents without the essentials, with a message a person can act on", () => {
    expect(() => normalizeGuide([])).toThrow(GuideError);
    expect(() => normalizeGuide("x")).toThrow(/JSON object/);
    expect(() => guide({ title: " " })).toThrow(/title/);
    expect(() => guide({ start: "5 March" })).toThrow(/dates like/);
    expect(() => guide({ start: "2027-02-30" })).toThrow(/dates like/);
    expect(() => guide({ end: "2027-03-01" })).toThrow(/ends before/);
    expect(() => guide({ days: [], places: [] })).toThrow(/no days and no places/);
  });

  it("drops what it can't use instead of failing the import", () => {
    const g = guide({
      places: [{ name: "" }, null, { name: "Bar", kind: "nightclub" }, { id: "bar", name: "Duplicate id" }],
      days: [{ date: "soon", stops: [] }, { date: "2027-03-06", stops: [{ title: "" }, { title: "Walk", kind: "walk", place: "nowhere" }] }],
      bookings: [{ title: "" }, { title: "Dinner", at: "eight-ish" }],
      tips: [{ title: "Empty", tips: [] }, { title: "", tips: [{ body: "x" }] }],
    });
    expect(g.places).toEqual([expect.objectContaining({ id: "bar", name: "Bar", kind: "sight", area: "Elsewhere" })]);
    expect(g.days).toHaveLength(1);
    expect(g.days[0].stops).toEqual([expect.objectContaining({ title: "Walk", place: "" })]); // unknown place ref cleared
    expect(g.bookings).toEqual([expect.objectContaining({ title: "Dinner", at: "", kind: "other" })]);
    expect(g.tips).toEqual([]);
  });

  it("derives a slug when the file has none, or an unusable one", () => {
    expect(guide({ slug: undefined }).slug).toBe("rome-2027-03-05");
    expect(guide({ slug: "Rome Trip!" }).slug).toBe("rome-2027-03-05");
    expect(normalizeGuide({ ...raw(), slug: "" }, "stored-slug").slug).toBe("stored-slug");
    expect(() => guide({ slug: "", title: "רומא" })).not.toThrow(); // the date alone still makes a slug
    expect(guide({ slug: "", title: "רומא" }).slug).toBe("2027-03-05");
  });

  it("keeps only web links", () => {
    expect(safeUrl("https://example.com/a b")).toBe("https://example.com/a%20b");
    expect(safeUrl("javascript:alert(1)")).toBe("");
    expect(safeUrl("//example.com")).toBe("");
    expect(safeUrl(" HTTP://Example.com ")).toBe("http://example.com/");
    const [p] = guide({ places: [{ name: "X", url: "javascript:alert(1)" }] }).places;
    expect(p.url).toBe("");
  });
});

describe("readGuides", () => {
  it("skips unreadable stored documents and falls back to the stored slug", () => {
    const stored = [
      { slug: "rome-2027-03", updated_at: "", data: raw() },
      { slug: "broken", updated_at: "", data: { title: "No dates" } },
      { slug: "from-row", updated_at: "", data: { ...raw(), slug: "" } },
    ];
    expect(readGuides(stored).map((g) => g.slug)).toEqual(["rome-2027-03", "from-row"]);
  });
});

describe("dates", () => {
  it("formats days and ranges", () => {
    expect(dayLabel("2026-11-24")).toBe("Tue 24 Nov");
    expect(dateRange("2026-11-21", "2026-11-26")).toBe("21–26 Nov 2026");
    expect(dateRange("2026-11-28", "2026-12-02")).toBe("28 Nov – 2 Dec 2026");
    expect(dateRange("2026-12-28", "2027-01-02")).toBe("28 Dec 2026 – 2 Jan 2027");
    expect(dateRange("2026-11-21", "2026-11-21")).toBe("21 Nov 2026");
    expect(daysBetween("2026-10-24", "2026-10-26")).toBe(2); // across a clock change
  });

  it("uses the device's own calendar day", () => {
    const lateEvening = new Date(2026, 10, 21, 23, 55);
    expect(todayISO(lateEvening)).toBe("2026-11-21");
    expect(nowLocal(lateEvening)).toBe("2026-11-21T23:55");
    expect(nowLocal(new Date(2026, 0, 5, 7, 5))).toBe("2026-01-05T07:05");
  });

  it("knows where in the trip today falls", () => {
    const g = { start: "2026-11-21", end: "2026-11-26" };
    expect(tripPhase(g, "2026-11-19")).toEqual({ phase: "before", daysToGo: 2 });
    expect(phaseLabel(tripPhase(g, "2026-11-20"))).toBe("Tomorrow");
    expect(phaseLabel(tripPhase(g, "2026-11-19"))).toBe("In 2 days");
    expect(tripPhase(g, "2026-11-21")).toEqual({ phase: "during", dayNumber: 1, totalDays: 6 });
    expect(phaseLabel(tripPhase(g, "2026-11-26"))).toBe("Day 6 of 6");
    expect(phaseLabel(tripPhase(g, "2026-11-27"))).toBe("Ended yesterday");
    expect(phaseLabel(tripPhase(g, "2026-12-01"))).toBe("Ended 5 days ago");
  });

  it("opens the plan on today while travelling, else the first day", () => {
    const g = guide();
    expect(defaultDayIndex(g, "2027-03-06")).toBe(1);
    expect(defaultDayIndex(g, "2027-03-01")).toBe(0);
    expect(defaultDayIndex(g, "2027-03-07")).toBe(0); // a day with no plan
  });

  it("finds the next booking", () => {
    const g = guide();
    expect(nextBooking(g, "2027-03-01T10:00")?.title).toBe("TLV → FCO");
    expect(nextBooking(g, "2027-03-05T06:11")?.title).toBe("Galleria Borghese");
    expect(nextBooking(g, "2027-03-06T17:01")).toBeNull();
  });
});

describe("pickGuide", () => {
  const g = (slug: string, start: string, end: string) => ({ ...guide(), slug, start, end });
  const all = [g("past", "2026-08-01", "2026-08-08"), g("soon", "2026-11-21", "2026-11-26"), g("later", "2027-03-05", "2027-03-08")];
  it("prefers a trip under way, then the next one, then the latest past one", () => {
    expect(pickGuide(all, "2026-11-23")?.slug).toBe("soon");
    expect(pickGuide(all, "2026-11-01")?.slug).toBe("soon");
    expect(pickGuide(all, "2026-12-01")?.slug).toBe("later");
    expect(pickGuide(all, "2027-06-01")?.slug).toBe("later");
    expect(pickGuide([], "2026-11-23")).toBeNull();
  });
});

describe("places", () => {
  it("lists the kinds present, in a fixed order", () => {
    expect(placeKinds(guide().places)).toEqual(["coffee", "sight", "market"]);
  });

  it("groups by area with top picks first", () => {
    const groups = groupByArea(guide({ places: [...raw().places, { name: "Roscioli Caffè", kind: "coffee", area: "Pantheon", pick: true }] }).places);
    expect(groups.map((x) => x.area)).toEqual(["Pantheon", "Centro", "Villa Borghese"]);
    expect(groups[0].places.map((p) => p.pick)).toEqual([true, true, false]);
  });

  it("knows which days stop at a place", () => {
    const planned = plannedDays(guide());
    expect(planned.get("eustachio")).toEqual(["Sat"]);
    expect(planned.has("tazza")).toBe(false);
  });

  it("builds maps links that survive odd names", () => {
    const p = { map: "Caffè & Co, 1 Main St" };
    expect(mapsUrl(p)).toBe("https://www.google.com/maps/search/?api=1&query=Caff%C3%A8%20%26%20Co%2C%201%20Main%20St");
    expect(directionsUrl(p)).toContain("destination=Caff%C3%A8%20%26%20Co%2C%201%20Main%20St&travelmode=transit");
  });
});

describe("helpers", () => {
  it("slugifies", () => {
    expect(slugify("London — Nov 2026!")).toBe("london-nov-2026");
    expect(slugify("Café Crème")).toBe("cafe-creme");
    expect(slugify("x".repeat(80))).toHaveLength(63);
  });

  it("reads the sub-view from the hash", () => {
    expect(viewFromHash("#guide")).toBe("plan");
    expect(viewFromHash("#guide/places")).toBe("places");
    expect(viewFromHash("#/guide/bookings")).toBe("bookings");
    expect(viewFromHash("#guide/nope")).toBe("plan");
    expect(viewFromHash("#trips/3")).toBe("plan");
  });
});
