// Client-side airport / metro / region search for the From and To fields.
// The dataset itself (airports-data.ts) is loaded on demand via loadAirports().

export type PlaceKind = "airport" | "metro" | "country" | "region";

export interface Place {
  kind: PlaceKind;
  /** IATA airport or metro code; "IST/SAW" for multi-airport cities without a metro code; "" for countries/regions. */
  code: string;
  city: string;
  /** Airport name, or the subtitle of a country/region. */
  name: string;
  country: string;
  /** What gets written into the form field. */
  value: string;
  top: boolean;
  /** Metro member airports, in display order. */
  members: Place[];
  /** Metro an airport belongs to. */
  metro: Place | null;
  order: number;
  keys: { codes: string[]; city: string; cityWords: string[]; name: string; nameWords: string[]; country: string; aliases: string[] };
}

export interface Suggestion {
  place: Place;
  /** An airport listed under its metro. */
  child: boolean;
}

export interface AirportIndex {
  places: Place[];
  byValue: Map<string, Place>;
}

const FOLD: Record<string, string> = { ł: "l", ø: "o", đ: "d", ß: "ss", æ: "ae", œ: "oe", ı: "i", þ: "th" };

/** Lowercase, accent-free, punctuation-free: "Zürich" → "zurich", "Xi'an" → "xian", "ז'נבה" → "זנבה". */
export function normalize(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{M}+/gu, "")
    .replace(/[łøđßæœıþ]/g, (c) => FOLD[c])
    .replace(/['’`"׳״]/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

const words = (s: string) => s.split(" ").filter(Boolean);

function makePlace(p: Omit<Place, "keys" | "members" | "metro"> & { aliases: string[] }): Place {
  const city = normalize(p.city);
  const name = normalize(p.name);
  const { aliases, ...rest } = p;
  return {
    ...rest,
    members: [],
    metro: null,
    keys: {
      codes: p.code ? [p.code.split("/")[0].toLowerCase()] : [],
      city,
      cityWords: words(city),
      name,
      nameWords: words(name),
      country: normalize(p.country),
      aliases: aliases.map(normalize).filter(Boolean),
    },
  };
}

const splitList = (s: string | undefined) => (s ? s.split(",").map((a) => a.trim()).filter(Boolean) : []);
const rows = (block: string) => block.split("\n").map((l) => l.trim()).filter(Boolean);

export function buildIndex(data: { COUNTRIES: Record<string, string>; AIRPORTS: string; METROS: string; REGIONS: string }): AirportIndex {
  const countries = new Map(
    Object.entries(data.COUNTRIES).map(([cc, row]) => {
      const [name, aliases] = row.split("|");
      return [cc, { name, aliases: splitList(aliases) }];
    }),
  );
  const places: Place[] = [];
  const byCode = new Map<string, Place>();

  for (const line of rows(data.AIRPORTS)) {
    const top = line.startsWith("*");
    const [code, city, name, cc, aliases] = (top ? line.slice(1) : line).split("|");
    const p = makePlace({
      kind: "airport",
      code,
      city,
      name,
      country: countries.get(cc)?.name ?? cc,
      value: `${city} [${code}]`,
      top,
      order: places.length,
      aliases: splitList(aliases),
    });
    places.push(p);
    byCode.set(code, p);
  }

  for (const line of rows(data.METROS)) {
    const [code, city, cc, members, aliases] = line.split("|");
    const m = makePlace({
      kind: "metro",
      code,
      city,
      name: "All airports",
      country: countries.get(cc)?.name ?? cc,
      value: `${city} [${code}]`,
      top: true,
      order: places.length,
      aliases: splitList(aliases),
    });
    for (const c of members.split(" ")) {
      const a = byCode.get(c);
      if (!a) throw new Error(`metro ${code}: unknown airport ${c}`);
      m.members.push(a);
      a.metro = m;
    }
    places.push(m);
  }

  for (const { name, aliases } of countries.values()) {
    places.push(
      makePlace({
        kind: "country",
        code: "",
        city: name,
        name: "Country — the agent compares its airports",
        country: "",
        value: name,
        top: false,
        order: places.length,
        aliases,
      }),
    );
  }

  for (const line of rows(data.REGIONS)) {
    const [name, subtitle, aliases] = line.split("|");
    places.push(
      makePlace({
        kind: "region",
        code: "",
        city: name,
        name: subtitle,
        country: "",
        value: name === "Anywhere" ? "anywhere" : name,
        top: false,
        order: places.length,
        aliases: splitList(aliases),
      }),
    );
  }

  return { places, byValue: new Map(places.map((p) => [p.value, p])) };
}

let loading: Promise<AirportIndex> | null = null;

/** Loads (once) and indexes the dataset. */
export function loadAirports(): Promise<AirportIndex> {
  if (!loading) {
    loading = import("./airports-data").then(buildIndex);
    loading.catch(() => (loading = null));
  }
  return loading;
}

/** Edit distance ≤ 1 (one substitution, insertion, deletion or adjacent swap)? */
function withinOne(a: string, b: string): boolean {
  if (a === b) return true;
  const la = a.length;
  const lb = b.length;
  if (Math.abs(la - lb) > 1) return false;
  let i = 0;
  while (i < la && i < lb && a[i] === b[i]) i++;
  if (la === lb) {
    if (a.slice(i + 1) === b.slice(i + 1)) return true;
    return a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2);
  }
  return la > lb ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
}

/** Typo-tolerant prefix: does `text` start with something within one edit of `q`? */
function fuzzyPrefix(text: string, q: string): boolean {
  for (const len of [q.length, q.length - 1, q.length + 1]) {
    if (len > 0 && len <= text.length && withinOne(text.slice(0, len), q)) return true;
  }
  return false;
}

const startsWord = (ws: string[], q: string) => ws.some((w) => w.startsWith(q));

function score(p: Place, q: string): number {
  const k = p.keys;
  let s = 0;
  if (q.length === 3 && k.codes[0] === q) s = 1000;
  else if (k.city === q) s = 800;
  else if (k.aliases.includes(q)) s = 750;
  else if (k.city.startsWith(q)) s = 700;
  else if (k.aliases.some((a) => a.startsWith(q))) s = 650;
  else if (startsWord(k.cityWords, q)) s = 600;
  else if (k.name.startsWith(q)) s = 550;
  else if (startsWord(k.nameWords, q) || (q.includes(" ") && k.name.includes(q))) s = 450;
  else if (q.length < 3 && k.codes[0]?.startsWith(q)) s = 400;
  else if (q.length >= 3 && k.country.startsWith(q)) s = 250;
  else if (q.length >= 4 && (fuzzyPrefix(k.city, q) || k.aliases.some((a) => fuzzyPrefix(a, q)))) s = 200;
  if (!s) return 0;
  if (p.kind === "metro") s += 20;
  if (p.kind === "country" || p.kind === "region") s -= 10;
  if (p.top) s += 30;
  return s;
}

/**
 * Ranked suggestions for `query`: exact IATA code first, then metros, then city
 * prefix, airport name, country, and finally one-typo matches. A metro pulls its
 * airports in right below it (child: true); `limit` counts top-level rows only.
 */
export function searchAirports(index: AirportIndex, query: string, limit = 8): Suggestion[] {
  const q = normalize(query);
  if (!q) return [];
  const hits: { p: Place; s: number }[] = [];
  for (const p of index.places) {
    const s = score(p, q);
    if (s) hits.push({ p, s });
  }
  hits.sort((a, b) => b.s - a.s || a.p.order - b.p.order);

  const matched = new Set(hits.map((h) => h.p));
  const out: Suggestion[] = [];
  let top = 0;
  for (const { p } of hits) {
    if (top >= limit) break;
    if (p.metro && matched.has(p.metro)) continue; // listed under its metro
    out.push({ place: p, child: false });
    top++;
    for (const m of p.members) out.push({ place: m, child: true });
  }
  return out;
}

const SEPARATOR = /,\s*|\s+or\s+/gi;

/** Split a free-text field into the part to keep and the segment being typed:
 *  "Prague, Vie" → { head: "Prague, ", term: "Vie" }. */
export function splitLastSegment(value: string): { head: string; term: string } {
  let last: RegExpExecArray | null = null;
  for (const m of value.matchAll(SEPARATOR)) last = m as RegExpExecArray;
  if (!last) return { head: "", term: value };
  const sep = last[0].includes(",") ? ", " : " or ";
  return { head: value.slice(0, last.index) + sep, term: value.slice(last.index + last[0].length) };
}

/** True when the segment is already a picked value like "Prague [PRG]". */
export const isPicked = (term: string) => /\[[A-Z]{3}(?:\/[A-Z]{3})*\]\s*$/.test(term);
