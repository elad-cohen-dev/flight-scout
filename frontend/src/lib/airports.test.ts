import { beforeAll, describe, expect, it } from "vitest";
import * as data from "./airports-data";
import { buildIndex, isPicked, normalize, searchAirports, splitLastSegment } from "./airports";
import type { AirportIndex } from "./airports";

let index: AirportIndex;
beforeAll(() => {
  index = buildIndex(data);
});

const codes = (q: string, limit?: number) => searchAirports(index, q, limit).map((s) => s.place.code || s.place.value);
const first = (q: string) => searchAirports(index, q)[0]?.place;

describe("dataset", () => {
  it("has unique, well-formed IATA codes and a sensible size", () => {
    const airports = index.places.filter((p) => p.kind === "airport");
    expect(airports.length).toBeGreaterThan(400);
    expect(airports.length).toBeLessThan(700);
    const seen = new Set<string>();
    for (const a of airports) {
      expect(a.code).toMatch(/^[A-Z]{3}$/);
      expect(seen.has(a.code), a.code).toBe(false);
      seen.add(a.code);
    }
  });

  it("has unique form values", () => {
    expect(index.byValue.size).toBe(index.places.length);
  });
});

describe("normalize", () => {
  it("folds accents, case and punctuation", () => {
    expect(normalize("Zürich")).toBe("zurich");
    expect(normalize("Łódź")).toBe("lodz");
    expect(normalize("Xi'an")).toBe("xian");
    expect(normalize("Cluj-Napoca")).toBe("cluj napoca");
    expect(normalize("ז'נבה")).toBe("זנבה");
  });
});

describe("searchAirports ranking", () => {
  it("puts an exact IATA code first", () => {
    expect(first("PRG")?.code).toBe("PRG");
    expect(first("lhr")?.code).toBe("LHR");
    expect(first("ber")?.code).toBe("BER");
    expect(first("saw")?.code).toBe("SAW");
  });

  it("puts a metro first and lists its airports under it", () => {
    const res = searchAirports(index, "london");
    expect(res[0].place.code).toBe("LON");
    expect(res.slice(1, 7).map((s) => [s.place.code, s.child])).toEqual(
      ["LHR", "LGW", "STN", "LTN", "LCY", "SEN"].map((c) => [c, true]),
    );
    // members don't repeat as standalone rows
    expect(res.filter((s) => s.place.code === "LHR")).toHaveLength(1);
    expect(codes("LON")[0]).toBe("LON");
    expect(codes("ist").slice(0, 3)).toEqual(["IST/SAW", "IST", "SAW"]);
  });

  it("matches city prefixes, accent-insensitively", () => {
    expect(first("prag")?.code).toBe("PRG");
    expect(first("zurich")?.code).toBe("ZRH");
    expect(first("dusseldorf")?.code).toBe("DUS");
    expect(first("krakow")?.code).toBe("KRK");
    expect(first("tel")?.code).toBe("TLV");
  });

  it("matches airport names and aliases", () => {
    expect(first("heathrow")?.code).toBe("LHR");
    expect(first("bergamo")?.code).toBe("BGY");
    expect(first("mallorca")?.code).toBe("PMI");
    expect(codes("crete")).toEqual(expect.arrayContaining(["HER", "CHQ"]));
  });

  it("tolerates one typo", () => {
    expect(first("budapset")?.code).toBe("BUD");
    expect(first("zurik")?.code).toBe("ZRH");
  });

  it("offers countries and regions", () => {
    expect(first("greece")?.value).toBe("Greece");
    expect(first("cyprus")?.value).toBe("Cyprus");
    expect(first("balk")?.value).toBe("Balkans");
    expect(first("anywh")?.value).toBe("anywhere");
    // airports in the country follow it
    expect(codes("greece")).toContain("ATH");
  });

  it("understands Hebrew city names", () => {
    expect(first("פראג")?.code).toBe("PRG");
    expect(first("לונדון")?.code).toBe("LON");
    expect(first("אתונה")?.code).toBe("ATH");
    expect(first("יוון")?.value).toBe("Greece");
  });

  it("returns nothing for empty or unknown input", () => {
    expect(searchAirports(index, "  ")).toEqual([]);
    expect(searchAirports(index, "qqqqzz")).toEqual([]);
  });

  it("respects the limit for top-level rows", () => {
    const res = searchAirports(index, "a", 5);
    expect(res.filter((s) => !s.child)).toHaveLength(5);
  });

  it("is fast", () => {
    const qs = ["l", "lo", "lon", "lond", "p", "pr", "pra", "budapset", "זנ", "gre"];
    const t0 = performance.now();
    for (let i = 0; i < 20; i++) for (const q of qs) searchAirports(index, q);
    const perQuery = (performance.now() - t0) / (20 * qs.length);
    expect(perQuery).toBeLessThan(5);
  });
});

describe("value format", () => {
  it("writes City [CODE], which survives the stored search label parsers", () => {
    expect(first("PRG")?.value).toBe("Prague [PRG]");
    expect(first("london")?.value).toBe("London [LON]");
    expect(first("ist")?.value).toBe("Istanbul [IST/SAW]");
    // storage.py / parseSearchQuery split the label on the first " (".
    const label = `Tel Aviv [TLV] -> ${first("PRG")!.value} (October, direct only)`;
    expect(label.split(" (")[0]).toBe("Tel Aviv [TLV] -> Prague [PRG]");
  });
});

describe("comma lists", () => {
  it("splits off the segment being typed", () => {
    expect(splitLastSegment("Prague")).toEqual({ head: "", term: "Prague" });
    expect(splitLastSegment("Prague [PRG], Vie")).toEqual({ head: "Prague [PRG], ", term: "Vie" });
    expect(splitLastSegment("Prague,Vie")).toEqual({ head: "Prague, ", term: "Vie" });
    expect(splitLastSegment("Greece or Cy")).toEqual({ head: "Greece or ", term: "Cy" });
  });

  it("recognises picked values", () => {
    expect(isPicked("Prague [PRG]")).toBe(true);
    expect(isPicked("Istanbul [IST/SAW]")).toBe(true);
    expect(isPicked("Prague")).toBe(false);
  });
});
