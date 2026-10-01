"""Airline identity: IATA code <-> canonical name, plus the aliases the web uses.

The extractor writes whatever the source page said ("EL AL", "El Al Israel
Airlines", "Wizz Air UK", "Israir Airlines"), so the same carrier showed up under
several names and deals never merged. `resolve()` maps any of those spellings —
or a code, or a flight-number prefix — to one `Airline`.

Subsidiaries sold under one brand share the brand's canonical name and `group`
but keep their own operating code: "Wizz Air UK" is W9, displayed "Wizz Air",
grouped with W6. Dedupe keys use the group (see `identity()`), so a W6 and a W9
sighting of the same fare are one deal.

Coverage: carriers flying to/from Israel, the main European network and
low-cost airlines, and the big global hubs. Unknown names pass through tidied
and are flagged by the normalizer, which is how this list should grow.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Airline:
    code: str  # operating IATA code
    name: str  # canonical display name
    group: str  # primary code of the brand family; == code for standalone carriers


# (codes, canonical name, aliases). The first code is the brand's primary code;
# further codes are subsidiaries/AOCs sold under the same brand. Aliases are
# matched after `_key()` normalization (lowercase, no accents/punctuation).
_CARRIERS: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = [
    # Israel
    (("LY",), "El Al", ("el al israel airlines", "elal", "el al airlines", "אל על")),
    (("IZ",), "Arkia", ("arkia israeli airlines", "arkia airlines", "ארקיע")),
    (("6H",), "Israir", ("israir airlines", "israir airline", "ישראייר")),
    (("BZ",), "Blue Bird Airways", ("blue bird", "bluebird", "bluebird airways")),
    # Low-cost Europe
    (("W6", "W4", "W9", "5W"), "Wizz Air", ("wizz", "wizzair", "wizz air hungary", "wizz air uk",
                                            "wizz air malta", "wizz air abu dhabi")),
    (("FR", "RK", "AL", "RR"), "Ryanair", ("ryan air", "ryanair uk", "malta air", "buzz", "ryanair dac")),
    (("U2", "EC", "DS"), "easyJet", ("easy jet", "easyjet europe", "easyjet switzerland", "easyjet uk")),
    (("V7",), "Volotea", ()),
    (("VY",), "Vueling", ("vueling airlines",)),
    (("HV", "TO"), "Transavia", ("transavia france", "transavia airlines")),
    (("EW",), "Eurowings", ()),
    (("DY", "D8", "DH"), "Norwegian", ("norwegian air", "norwegian air shuttle", "norwegian air sweden",
                                       "norwegian air international")),
    (("LS",), "Jet2", ("jet2com", "jet2 com")),
    (("PC",), "Pegasus Airlines", ("pegasus", "flypgs")),
    (("XQ",), "SunExpress", ("sun express",)),
    (("VF",), "AJet", ("anadolujet", "anadolu jet", "a jet")),
    (("XC",), "Corendon Airlines", ("corendon",)),
    (("SR",), "Sundair", ()),
    (("5F",), "FlyOne", ("fly one",)),
    (("G9",), "Air Arabia", ()),
    (("FZ",), "flydubai", ("fly dubai",)),
    (("BT",), "airBaltic", ("air baltic",)),
    (("QS",), "Smartwings", ("smart wings", "travel service")),
    (("U8",), "TUS Airways", ("tus", "tus air")),
    (("BY",), "TUI Airways", ("tui", "tui uk")),
    (("X3",), "TUIfly", ("tui fly", "tuifly deutschland")),
    (("TB",), "TUI fly Belgium", ("tui belgium",)),
    (("OR",), "TUI fly Netherlands", ("tui netherlands",)),
    (("DE",), "Condor", ("condor airlines",)),
    (("4Y",), "Discover Airlines", ("discover",)),
    (("NO",), "Neos", ("neos air",)),
    (("DI",), "Marabu", ("marabu airlines",)),
    (("GQ",), "Sky Express", ("skyexpress",)),
    # European network carriers
    (("LH",), "Lufthansa", ("deutsche lufthansa",)),
    (("LX",), "SWISS", ("swiss international air lines", "swiss international airlines", "swiss air lines",
                        "swiss air")),
    (("OS",), "Austrian Airlines", ("austrian",)),
    (("SN",), "Brussels Airlines", ()),
    (("EN",), "Air Dolomiti", ()),
    (("WK",), "Edelweiss", ("edelweiss air",)),
    (("BA",), "British Airways", ()),
    (("VS",), "Virgin Atlantic", ("virgin",)),
    (("EI",), "Aer Lingus", ()),
    (("AF",), "Air France", ()),
    (("KL",), "KLM", ("klm royal dutch airlines", "royal dutch airlines")),
    (("AZ",), "ITA Airways", ("ita", "alitalia")),
    (("IB",), "Iberia", ("iberia airlines",)),
    (("I2",), "Iberia Express", ()),
    (("UX",), "Air Europa", ()),
    (("TP",), "TAP Air Portugal", ("tap", "tap portugal", "tap air")),
    (("SK",), "SAS", ("scandinavian airlines", "sas scandinavian airlines")),
    (("AY",), "Finnair", ()),
    (("FI",), "Icelandair", ()),
    (("LG",), "Luxair", ()),
    (("KM",), "KM Malta Airlines", ("malta airlines", "air malta")),
    (("A3",), "Aegean Airlines", ("aegean", "aegean air")),
    (("OA",), "Olympic Air", ("olympic", "olympic airlines")),
    (("CY",), "Cyprus Airways", ()),
    (("LO",), "LOT Polish Airlines", ("lot", "polish airlines", "lot polish")),
    (("OK",), "Czech Airlines", ("csa", "csa czech airlines")),
    (("RO",), "TAROM", ("tarom romanian air transport",)),
    (("FB",), "Bulgaria Air", ()),
    (("JU",), "Air Serbia", ()),
    (("OU",), "Croatia Airlines", ()),
    (("ZB",), "Air Albania", ()),
    (("9U",), "Air Moldova", ()),
    (("TK",), "Turkish Airlines", ("turkish", "thy", "turk hava yollari")),
    (("A9",), "Georgian Airways", ()),
    (("J2",), "Azerbaijan Airlines", ("azal",)),
    (("HY",), "Uzbekistan Airways", ()),
    (("KC",), "Air Astana", ()),
    (("SU",), "Aeroflot", ()),
    # Middle East & Africa
    (("EK",), "Emirates", ("fly emirates", "emirates airline")),
    (("QR",), "Qatar Airways", ("qatar",)),
    (("EY",), "Etihad Airways", ("etihad",)),
    (("GF",), "Gulf Air", ()),
    (("WY",), "Oman Air", ()),
    (("SV",), "Saudia", ("saudi arabian airlines", "saudi airlines")),
    (("RJ",), "Royal Jordanian", ("royal jordanian airlines",)),
    (("MS",), "EgyptAir", ("egypt air",)),
    (("ET",), "Ethiopian Airlines", ("ethiopian",)),
    (("KQ",), "Kenya Airways", ()),
    (("AT",), "Royal Air Maroc", ("ram",)),
    (("HM",), "Air Seychelles", ()),
    (("MK",), "Air Mauritius", ()),
    # Global
    (("AA",), "American Airlines", ("american",)),
    (("UA",), "United Airlines", ("united",)),
    (("DL",), "Delta Air Lines", ("delta", "delta airlines")),
    (("AC",), "Air Canada", ()),
    (("B6",), "JetBlue", ("jetblue airways", "jet blue")),
    (("AI",), "Air India", ()),
    (("6E",), "IndiGo", ("indigo airlines",)),
    (("SQ",), "Singapore Airlines", ()),
    (("CX",), "Cathay Pacific", ("cathay",)),
    (("TG",), "Thai Airways", ("thai", "thai airways international")),
    (("VN",), "Vietnam Airlines", ()),
    (("HU",), "Hainan Airlines", ("hainan",)),
    (("CA",), "Air China", ()),
    (("MU",), "China Eastern", ("china eastern airlines",)),
    (("KE",), "Korean Air", ()),
    (("NH",), "ANA", ("all nippon airways",)),
    (("JL",), "Japan Airlines", ("jal",)),
    (("QF",), "Qantas", ()),
    (("LA",), "LATAM", ("latam airlines",)),
]

MULTIPLE = "Multiple airlines"
_MULTIPLE_WORDS = {"multiple", "multiple airlines", "multiple carriers", "various", "various airlines",
                   "mixed", "mixed carriers", "several", "several airlines", "combination", "multi"}

# Words that differ between spellings of the same carrier name.
_SUFFIX = re.compile(r"\s+(airlines?|airways|air lines|aviation|international|intl|co|ltd|plc)$")


def _key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_indexes() -> tuple[dict[str, Airline], dict[str, Airline]]:
    by_code: dict[str, Airline] = {}
    by_name: dict[str, Airline] = {}
    for codes, name, aliases in _CARRIERS:
        group = codes[0]
        for code in codes:
            by_code.setdefault(code, Airline(code=code, name=name, group=group))
        primary = by_code[group]
        for alias in (name, *aliases):
            by_name.setdefault(_key(alias), primary)
    # Subsidiary-specific names resolve to their own operating code.
    for alias, code in {"wizz air uk": "W9", "wizz air malta": "W4", "wizz air abu dhabi": "5W",
                        "ryanair uk": "RK", "malta air": "AL", "buzz": "RR",
                        "easyjet europe": "EC", "easyjet switzerland": "DS",
                        "norwegian air sweden": "D8", "transavia france": "TO"}.items():
        by_name[_key(alias)] = by_code[code]
    return by_code, by_name


BY_CODE, BY_NAME = _build_indexes()

_FLIGHT = re.compile(r"^\s*([A-Z0-9]{2})\s*-?\s*0*(\d{1,4})\s*([A-Z]?)\s*$", re.IGNORECASE)
_SPLIT_CARRIERS = re.compile(r"\s*(?:\+|/|,|&|;|\band\b|\bwith\b)\s*", re.IGNORECASE)


def by_code(code: str) -> Airline | None:
    return BY_CODE.get((code or "").strip().upper())


def _by_name_one(text: str) -> Airline | None:
    key = _key(text)
    if not key:
        return None
    if key in BY_NAME:
        return BY_NAME[key]
    stripped = _SUFFIX.sub("", key)
    while stripped != key:  # "israir airlines" -> "israir"; "x airways intl" -> "x"
        if stripped in BY_NAME:
            return BY_NAME[stripped]
        key, stripped = stripped, _SUFFIX.sub("", stripped)
    # "Wizz Air (W6)" / "El Al LY": a code riding along with the name.
    m = re.search(r"\(([A-Z0-9]{2})\)", text or "")
    if m and by_code(m.group(1)):
        return by_code(m.group(1))
    return None


def is_multiple(text: str) -> bool:
    return _key(text) in _MULTIPLE_WORDS


def split_carriers(text: str) -> list[str]:
    """'Arkia + Israir' -> ['Arkia', 'Israir']; a single name -> [name]."""
    if _by_name_one(text):
        return [text.strip()]
    parts = [p for p in _SPLIT_CARRIERS.split(text or "") if p.strip()]
    return parts or ([text.strip()] if text and text.strip() else [])


def flight_prefix(flight_numbers: str) -> str:
    """Carrier code of the first flight number: 'W6 2328 / W6 2327' -> 'W6'."""
    first = re.split(r"[/,;&+]", flight_numbers or "")[0]
    m = _FLIGHT.match(first)
    if not m or not re.search(r"[A-Z]", m.group(1), re.IGNORECASE):
        return ""
    return m.group(1).upper()


@lru_cache(maxsize=4096)
def resolve(name: str, code: str = "", flight_numbers: str = "") -> tuple[str, str, tuple[Airline | None, ...]]:
    """(display name, primary operating code, per-carrier matches) for a deal's airline fields.

    Name wins over code for the display name ("EL AL" -> "El Al"); an explicit code
    wins for the operating code; otherwise the first flight number's prefix, then
    the first carrier named. A multi-carrier string keeps every name ("Arkia +
    Israir") with the code of the first. Unknown names come back tidied, not
    replaced, with a None match so callers can flag them.
    """
    name = re.sub(r"\s+", " ", (name or "")).strip()
    explicit = by_code(code)
    prefix = by_code(flight_prefix(flight_numbers))
    if is_multiple(name):
        return MULTIPLE, (explicit or prefix).code if (explicit or prefix) else "", ()
    if not name:
        carrier = explicit or prefix
        return (carrier.name, carrier.code, (carrier,)) if carrier else ("", (code or "").strip().upper(), ())

    parts = split_carriers(name)
    matches = tuple(_by_name_one(p) for p in parts)
    names = [m.name if m else p.strip() for p, m in zip(parts, matches)]
    # Keep order, drop repeats ("Wizz Air / Wizz Air UK" is one brand).
    display = " + ".join(dict.fromkeys(names))

    first = matches[0]
    if explicit:
        op_code = explicit.code
    elif prefix and (first is None or prefix.group == first.group):
        op_code = prefix.code  # the flight number names the exact operator (W9 vs W6)
    elif first:
        op_code = first.code
    else:
        op_code = (code or "").strip().upper()
    return display, op_code, matches


def identity(name: str, code: str = "", flight_numbers: str = "") -> str:
    """Stable dedupe key for a deal's carrier: the brand group's code where known
    ('EL AL', 'El Al' and 'LY' -> 'LY'; 'Wizz Air UK' -> 'W6'), else the
    lowercased name."""
    display, op_code, matches = resolve(name, code, flight_numbers)
    if display == MULTIPLE:
        return "multiple"
    carrier = by_code(op_code) or (matches[0] if matches else None)
    if carrier and len(matches) <= 1:
        return carrier.group
    if matches and all(matches):
        return "+".join(m.group for m in matches)
    return _key(display)


def normalize_flight_numbers(text: str) -> tuple[str, list[str]]:
    """('W6 2328 / W6 2327', unparsed tokens) from 'w62328, W6-2327'. Leading zeros
    are dropped ('LY0315' -> 'LY 315'); unparseable tokens are kept verbatim."""
    raw = (text or "").strip()
    if not raw or raw.lower() in {"unknown", "n/a", "na", "-", "none", "tbd"}:
        return "", []
    tokens = [t for t in re.split(r"\s*(?:/|,|;|&|\+|\band\b|\bthen\b)\s*", raw, flags=re.IGNORECASE) if t]
    out: list[str] = []
    bad: list[str] = []
    for token in tokens:
        m = _FLIGHT.match(token)
        if m and re.search(r"[A-Z]", m.group(1), re.IGNORECASE):
            out.append(f"{m.group(1).upper()} {int(m.group(2))}{m.group(3).upper()}")
        else:
            out.append(token.strip())
            bad.append(token.strip())
    return " / ".join(out), bad
