import { describe, expect, it } from "vitest";
import { activeHoliday, holidayDay, holidayFormPatch, holidayName, holidayRange, pickable } from "./holidays";
import { DEFAULT_FORM, formToRequest, requestToForm } from "./search";
import type { HolidayWindow } from "../types";

const hanukkah: HolidayWindow = {
  id: "hanukkah-2026", name: "Hanukkah 2026", kind: "hanukkah", school_year: "2026-27",
  start: "2026-12-05", end: "2026-12-12", holiday_start: "2026-12-05", holiday_end: "2026-12-12",
  nights_min: 4, nights_max: 7, depart_from: "2026-12-05", depart_to: "2026-12-08", confirmed: true,
  note: "", sources: ["https://www.hebcal.com"],
};
const newYear = { ...hanukkah, id: "hanukkah-2027", name: "Hanukkah 2027", start: "2027-12-25", end: "2028-01-01" };

describe("holiday quick-picks", () => {
  it("formats the vacation's days", () => {
    expect(holidayRange(hanukkah)).toBe("5–12 Dec");
    expect(holidayRange(newYear)).toBe("25 Dec – 1 Jan");
    expect(holidayRange({ start: "2027-05-12", end: "2027-05-12" })).toBe("12 May");
    expect(holidayDay("2027-08-31")).toBe("31 Aug");
  });

  it("fills the exact date window, trip length and name", () => {
    const form = { ...DEFAULT_FORM, ...holidayFormPatch(hanukkah) };
    expect(form).toMatchObject({ date_mode: "exact", depart_from: "2026-12-05", depart_to: "2026-12-08",
      nights_min: "4", nights_max: "7", holiday: "hanukkah-2026", dates_hint: "Hanukkah 2026" });
    expect(holidayFormPatch(hanukkah, false)).toMatchObject({ nights_min: "", nights_max: "" });
  });

  it("sends the holiday and its name with the search, and reads them back", () => {
    const req = formToRequest({ ...DEFAULT_FORM, ...holidayFormPatch(hanukkah) });
    expect(req).toMatchObject({ holiday: "hanukkah-2026", dates_hint: "Hanukkah 2026", depart_from: "2026-12-05",
      depart_to: "2026-12-08", nights_min: 4, nights_max: 7 });
    const back = requestToForm(req);
    expect(back).toMatchObject({ holiday: "hanukkah-2026", dates_hint: "Hanukkah 2026", date_mode: "exact" });
  });

  it("drops the holiday in flexible mode", () => {
    const req = formToRequest({ ...DEFAULT_FORM, ...holidayFormPatch(hanukkah), date_mode: "flexible" });
    expect(req.holiday).toBeUndefined();
    const plain = formToRequest({ ...DEFAULT_FORM, date_mode: "exact", depart_from: "2026-12-01" });
    expect(plain.holiday).toBeUndefined();
    expect(plain.dates_hint).toBe("specific dates");
  });

  it("knows which holiday is active", () => {
    const form = { ...DEFAULT_FORM, ...holidayFormPatch(hanukkah) };
    expect(activeHoliday(form, [hanukkah, newYear])?.id).toBe("hanukkah-2026");
    expect(activeHoliday({ ...form, date_mode: "flexible" }, [hanukkah])).toBeNull();
    expect(activeHoliday(form, [newYear])).toBeNull();
    expect(activeHoliday(DEFAULT_FORM, [hanukkah])).toBeNull();
  });

  it("names a holiday from its id", () => {
    expect(holidayName("hanukkah-2026")).toBe("Hanukkah 2026");
    expect(holidayName("summer-2027")).toBe("Summer vacation 2027");
    expect(holidayName("rosh-hashana-2027")).toBe("Rosh Hashana 2027");
    expect(holidayName("independence-day-2028")).toBe("Independence Day 2028");
    expect(holidayName("lag-baomer-2027")).toBe("Lag baomer 2027");
    expect(holidayName("")).toBe("");
    expect(holidayName(undefined)).toBe("");
    expect(holidayName("not a holiday")).toBe("");
  });

  it("offers the next few", () => {
    expect(pickable(Array(10).fill(hanukkah)).length).toBe(6);
    expect(pickable([hanukkah], 6)).toEqual([hanukkah]);
  });
});
