"""Airport lookup for the backend, built from the frontend's curated dataset.

`data/airports.json` is generated from frontend/src/lib/airports-data.ts by
scripts/export_airports.py — edit the .ts file and re-export, never the JSON.
A test fails when the two drift apart.

The dataset has no coordinates, so distances (used only to sanity-check prices)
come from one representative point per country — its main international hub.
That is coarse on purpose: the price check only needs a distance *band*.
Per-country currency lives here too; both tables are backend-only.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "airports.json"


@dataclass(frozen=True)
class Place:
    code: str  # IATA airport or metro code; pseudo-metros are "IST/SAW"
    city: str
    country: str  # ISO 3166 alpha-2
    kind: str  # "airport" | "metro"
    members: tuple[str, ...] = field(default=())  # airport codes a metro covers; (code,) for an airport

    @property
    def iata(self) -> str:
        """The code when it is a real IATA code (airport or metro like LON), else ''."""
        return self.code if re.fullmatch(r"[A-Z]{3}", self.code) else ""


# Main hub per country (lat, lon) and local currency. ISO 4217 codes.
COUNTRY_INFO: dict[str, tuple[float, float, str]] = {
    "IL": (32.0, 34.9, "ILS"), "CY": (34.9, 33.6, "EUR"), "GR": (37.9, 23.9, "EUR"), "TR": (41.3, 28.7, "TRY"),
    "GB": (51.5, -0.3, "GBP"), "IE": (53.4, -6.3, "EUR"), "FR": (49.0, 2.5, "EUR"), "BE": (50.9, 4.5, "EUR"),
    "NL": (52.3, 4.8, "EUR"), "LU": (49.6, 6.2, "EUR"), "DE": (50.0, 8.6, "EUR"), "CH": (47.5, 8.5, "CHF"),
    "AT": (48.1, 16.6, "EUR"), "IT": (41.8, 12.3, "EUR"), "ES": (40.5, -3.6, "EUR"), "PT": (38.8, -9.1, "EUR"),
    "MT": (35.9, 14.5, "EUR"), "GI": (36.2, -5.3, "GBP"), "DK": (55.6, 12.6, "DKK"), "SE": (59.6, 17.9, "SEK"),
    "NO": (60.2, 11.1, "NOK"), "FI": (60.3, 24.9, "EUR"), "IS": (64.0, -22.6, "ISK"), "LT": (54.6, 25.3, "EUR"),
    "LV": (56.9, 24.0, "EUR"), "EE": (59.4, 24.8, "EUR"), "PL": (52.2, 21.0, "PLN"), "CZ": (50.1, 14.3, "CZK"),
    "SK": (48.2, 17.2, "EUR"), "HU": (47.4, 19.3, "HUF"), "RO": (44.6, 26.1, "RON"), "MD": (46.9, 28.9, "MDL"),
    "BG": (42.7, 23.4, "EUR"), "RS": (44.8, 20.3, "RSD"), "ME": (42.4, 19.3, "EUR"), "HR": (45.7, 16.1, "EUR"),
    "SI": (46.2, 14.5, "EUR"), "BA": (43.8, 18.3, "BAM"), "MK": (42.0, 21.6, "MKD"), "AL": (41.4, 19.7, "ALL"),
    "XK": (42.6, 21.0, "EUR"), "GE": (41.7, 45.0, "GEL"), "AM": (40.1, 44.4, "AMD"), "AZ": (40.5, 50.0, "AZN"),
    "RU": (56.0, 37.4, "RUB"), "BY": (53.9, 28.0, "BYN"), "UZ": (41.3, 69.3, "UZS"), "KZ": (43.4, 77.0, "KZT"),
    "KG": (43.1, 74.5, "KGS"), "AE": (25.3, 55.4, "AED"), "BH": (26.3, 50.6, "BHD"), "QA": (25.3, 51.6, "QAR"),
    "OM": (23.6, 58.3, "OMR"), "SA": (25.0, 46.7, "SAR"), "KW": (29.2, 48.0, "KWD"), "JO": (31.7, 36.0, "JOD"),
    "EG": (30.1, 31.4, "EGP"), "MA": (33.4, -7.6, "MAD"), "TN": (36.9, 10.2, "TND"), "ZA": (-26.1, 28.2, "ZAR"),
    "KE": (-1.3, 36.9, "KES"), "ET": (9.0, 38.8, "ETB"), "TZ": (-6.9, 39.2, "TZS"), "MU": (-20.4, 57.7, "MUR"),
    "SC": (-4.7, 55.5, "SCR"), "NG": (6.6, 3.3, "NGN"), "GH": (5.6, -0.2, "GHS"), "RW": (-2.0, 30.1, "RWF"),
    "UG": (0.0, 32.4, "UGX"), "NA": (-22.5, 17.5, "NAD"), "ZW": (-17.9, 31.1, "USD"), "MG": (-18.8, 47.5, "MGA"),
    "SN": (14.7, -17.1, "XOF"), "RE": (-20.9, 55.5, "EUR"), "IN": (28.6, 77.1, "INR"), "LK": (7.2, 79.9, "LKR"),
    "MV": (4.2, 73.5, "MVR"), "NP": (27.7, 85.4, "NPR"), "TH": (13.7, 100.7, "THB"), "VN": (10.8, 106.7, "VND"),
    "KH": (11.5, 104.8, "KHR"), "LA": (18.0, 102.6, "LAK"), "MM": (16.9, 96.1, "MMK"), "MY": (2.7, 101.7, "MYR"),
    "SG": (1.4, 104.0, "SGD"), "ID": (-8.7, 115.2, "IDR"), "PH": (14.5, 121.0, "PHP"), "HK": (22.3, 113.9, "HKD"),
    "MO": (22.2, 113.6, "MOP"), "TW": (25.1, 121.2, "TWD"), "CN": (40.1, 116.6, "CNY"), "JP": (35.6, 139.8, "JPY"),
    "KR": (37.5, 126.4, "KRW"), "MN": (47.8, 106.8, "MNT"), "AU": (-33.9, 151.2, "AUD"), "NZ": (-37.0, 174.8, "NZD"),
    "FJ": (-17.8, 177.4, "FJD"), "PF": (-17.6, -149.6, "XPF"), "US": (40.6, -73.8, "USD"), "CA": (43.7, -79.6, "CAD"),
    "MX": (19.4, -99.1, "MXN"), "DO": (18.4, -69.7, "DOP"), "CU": (23.0, -82.4, "CUP"), "JM": (18.5, -77.9, "JMD"),
    "BS": (25.0, -77.5, "BSD"), "AW": (12.5, -70.0, "AWG"), "CW": (12.2, -69.0, "XCG"), "BB": (13.1, -59.5, "BBD"),
    "SX": (18.0, -63.1, "XCG"), "PR": (18.4, -66.0, "USD"), "PA": (9.1, -79.4, "PAB"), "CR": (10.0, -84.2, "CRC"),
    "GT": (14.6, -90.5, "GTQ"), "SV": (13.4, -89.1, "USD"), "CO": (4.7, -74.1, "COP"), "PE": (-12.0, -77.1, "PEN"),
    "CL": (-33.4, -70.8, "CLP"), "AR": (-34.8, -58.5, "ARS"), "BR": (-23.4, -46.5, "BRL"), "EC": (-0.1, -78.4, "USD"),
    "UY": (-34.8, -56.0, "UYU"), "PY": (-25.2, -57.5, "PYG"), "BO": (-16.5, -68.2, "BOB"), "VE": (10.6, -67.0, "VES"),
}


def _key(text: str) -> str:
    """Lowercase, accent-free, punctuation-free lookup key ('São Paulo' -> 'sao paulo')."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_GENERIC_NAMES = {"international", "airport", "intl"}


@dataclass
class _Index:
    countries: dict
    airports: dict[str, Place]
    metros: dict[str, Place]
    names: dict[str, Place]  # lookup key -> place
    city_airports: dict[str, list[tuple[str, Place]]]  # city key -> [(airport name key, place)]


def _load() -> _Index:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    airports: dict[str, Place] = {}
    by_city: dict[str, list[Place]] = {}
    city_airports: dict[str, list[tuple[str, Place]]] = {}
    alias_hits: dict[str, list[tuple[bool, Place]]] = {}  # (top destination, place)
    combos: dict[str, Place] = {}
    for row in data["airports"]:
        place = Place(row["code"], row["city"], row["country"], "airport", (row["code"],))
        airports[place.code] = place
        by_city.setdefault(_key(place.city), []).append(place)
        city_airports.setdefault(_key(place.city), []).append((_key(row["name"]), place))
        for alias in row["aliases"]:
            alias_hits.setdefault(_key(alias), []).append((row["top"], place))
        name = _key(row["name"])
        if name and name not in _GENERIC_NAMES and name != _key(place.city):
            # "Luton", "London Luton", "Ben Gurion", "Larnaca International"
            for combo in (name, f"{_key(place.city)} {name}", f"{name} {_key(place.city)}"):
                combos.setdefault(combo, place)

    metros: dict[str, Place] = {}
    metro_by_city: dict[str, Place] = {}
    for row in data["metros"]:
        place = Place(row["code"], row["city"], row["country"], "metro", tuple(row["members"]))
        metros[place.code] = place
        metro_by_city[_key(place.city)] = place
        for alias in row["aliases"]:
            metro_by_city.setdefault(_key(alias), place)

    names: dict[str, Place] = {}
    # Lowest precedence first; later updates win.
    names.update(combos)
    for key, hits in alias_hits.items():
        # An alias shared by several airports ("Crete": HER/CHQ/JSH) names none of them.
        if len({place.code for _, place in hits}) == 1:
            names[key] = hits[0][1]
    for key, places in by_city.items():
        names[key] = places[0]  # a single-airport city, or the dataset's main airport first
    names.update(metro_by_city)  # a multi-airport city is its metro
    return _Index(data["countries"], airports, metros, names, city_airports)


@lru_cache(maxsize=1)
def index() -> _Index:
    return _load()


def airport(code: str) -> Place | None:
    """An airport or a metro by code ('TLV', 'LON', 'IST/SAW')."""
    code = (code or "").strip().upper()
    idx = index()
    return idx.airports.get(code) or idx.metros.get(code)


def is_known_code(code: str) -> bool:
    return airport(code) is not None


_CODE_IN_TEXT = (re.compile(r"\(([A-Z]{3})\)"), re.compile(r"\[([A-Z]{3})\b"), re.compile(r"\b([A-Z]{3})\b"))
_NOISE = re.compile(r"\b(airport|international|intl|city|area|all airports|any)\b")


@lru_cache(maxsize=4096)
def resolve(text: str) -> Place | None:
    """The place a free-text origin/destination names, or None.

    Accepts codes ('TLV', 'lon'), 'City (XXX)', the autocomplete's 'City [XXX]',
    city names and unambiguous aliases (incl. Hebrew; 'Crete' spans three airports
    so it resolves to nothing), airport names ('Luton', 'London Luton',
    'Ben Gurion') and 'City, Country'. Multi-airport cities resolve to their metro.
    """
    s = (text or "").strip()
    if not s:
        return None
    idx = index()
    if re.fullmatch(r"[A-Z]{3}(/[A-Z]{3})*", s):
        place = airport(s)
        if place:
            return place
    key = _key(s)
    if key in idx.names:
        return idx.names[key]
    head = _key(s.split(",")[0])
    if head in idx.names:
        return idx.names[head]
    cleaned = re.sub(r"\s+", " ", _NOISE.sub(" ", head)).strip()
    if cleaned in idx.names:
        return idx.names[cleaned]
    # "Paris Beauvais", "Milan Bergamo": city + the start of an airport's name.
    for city, airports_of_city in idx.city_airports.items():
        if cleaned.startswith(city + " "):
            rest = cleaned[len(city) + 1:]
            for name, place in airports_of_city:
                if name.startswith(rest) or rest.split()[0] in name.split():
                    return place
    for pattern in _CODE_IN_TEXT:
        for code in pattern.findall(s):
            place = airport(code)
            if place:
                return place
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return airport(s)
    return None


def contains(place: Place, code: str) -> bool:
    """True when `code` is `place` or one of its member airports."""
    code = (code or "").upper()
    return code == place.code or code in place.members


def country_currency(country: str) -> str:
    info = COUNTRY_INFO.get(country)
    return info[2] if info else ""


def distance_km(a: Place, b: Place) -> float | None:
    """Great-circle distance between the two places' country hubs; None if unknown.
    Same country -> a nominal 300 km domestic hop."""
    if a.country == b.country:
        return 300.0
    pa, pb = COUNTRY_INFO.get(a.country), COUNTRY_INFO.get(b.country)
    if not pa or not pb:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (pa[0], pa[1], pb[0], pb[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))
