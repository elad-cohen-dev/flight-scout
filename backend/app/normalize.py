"""Deterministic cleanup of extracted deals: canonical names and codes, sane
currencies and prices, ISO dates. Applied to every fresh deal in
agent.postprocess and, once, to stored rows by scripts/repair_deals.py.

    normalize_deal(deal, params, today) -> (cleaned deal, quality flags)

The cleaned deal carries the flags in `quality_flags` too. Flags name problems
the data still has (or a correction worth a second look), e.g.
"currency_inferred", "price_too_low", "destination_airport_unknown"; the full
vocabulary is FLAGS below. Deals whose flags intersect FATAL can't be shown at
all (no price, no route) and are dropped by `normalize_deals`; everything else
is kept and flagged. Normalizing an already-normalized deal changes nothing.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from . import airlines, airports
from .currency import convert as _convert_currency
from .models import FlightDeal, SearchParams

logger = logging.getLogger(__name__)

FLAGS = {
    "no_price": "price missing, zero or negative (fatal)",
    "no_route": "neither origin nor destination known (fatal)",
    "airline_missing": "no airline name or code",
    "airline_unrecognized": "airline name not in the airline table",
    "airline_code_invalid": "airline code is not a 2-character IATA code (cleared)",
    "flight_airline_mismatch": "flight-number prefix belongs to a different airline",
    "flight_number_invalid": "a flight number couldn't be parsed",
    "origin_airport_invalid": "origin airport code is not an IATA code (cleared)",
    "destination_airport_invalid": "destination airport code is not an IATA code (cleared)",
    "origin_airport_unknown": "origin airport code not in the airport dataset",
    "destination_airport_unknown": "destination airport code not in the airport dataset",
    "origin_mismatch": "origin city and origin airport disagree",
    "destination_mismatch": "destination city and destination airport disagree",
    "currency_invalid": "currency is not an ISO 4217 code and couldn't be inferred",
    "currency_unusual": "valid currency, but unexpected for this route and source",
    "currency_inferred": "currency was invalid/implausible and was replaced from evidence (sticky)",
    "price_too_low": "per-person price implausibly low for the distance/cabin",
    "price_too_high": "per-person price implausibly high for the distance/cabin",
    "depart_date_missing": "no departure date",
    "depart_date_inexact": "departure is not one exact date (e.g. 'mid October')",
    "return_before_depart": "return date earlier than departure",
}
FATAL = {"no_price", "no_route"}
# Flags that record a past correction: the evidence is gone once the field is
# fixed, so re-normalizing must carry them forward rather than recompute them.
STICKY = {"currency_inferred"}

# Active ISO 4217 codes (2026).
ISO_4217 = set("""
AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP
GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD
KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN
NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP
SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND
VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG ANG
""".split())

# Quoted on most sites that sell flights from anywhere.
_GLOBAL_CURRENCIES = {"USD", "EUR", "GBP"}

_SYMBOLS = [("US$", "USD"), ("₪", "ILS"), ("€", "EUR"), ("£", "GBP"), ("Kč", "CZK"), ("zł", "PLN"),
            ("NIS", "ILS"), ("$", "USD")]
_SYMBOL_RE = "|".join(re.escape(s) for s, _ in _SYMBOLS)
_AMOUNT = r"(\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
# "€120", "EUR 120", "120 EUR", "120€", "₪ 1,009". "$" must not be part of "HK$"/"A$"/"CA$".
_MONEY = re.compile(
    rf"(?<![A-Za-z])(?P<pre>{_SYMBOL_RE}|[A-Z]{{3}})\s?{_AMOUNT}"
    rf"|{_AMOUNT}\s?(?P<post>{_SYMBOL_RE}|[A-Z]{{3}})(?![A-Za-z])"
)

_TLD_CURRENCY = {
    "il": "ILS", "uk": "GBP", "cz": "CZK", "pl": "PLN", "hu": "HUF", "ro": "RON", "ch": "CHF",
    "se": "SEK", "no": "NOK", "dk": "DKK", "tr": "TRY", "ae": "AED", "ge": "GEL", "rs": "RSD",
    **{tld: "EUR" for tld in ("de", "fr", "it", "es", "gr", "nl", "be", "at", "pt", "ie", "fi", "sk",
                              "si", "ee", "lv", "lt", "lu", "mt", "cy", "hr", "bg")},
}

# Rough per-person, one-way, economy bounds in USD by great-circle distance (km).
_PRICE_BANDS = [  # (max km, low, high)
    (1000, 12, 900),
    (2500, 15, 1500),
    (5000, 20, 2500),
    (9000, 60, 4500),
    (math.inf, 100, 7000),
]
_CABIN_FACTOR = {"economy": (1, 1), "premium_economy": (1.5, 2), "business": (3, 5), "first": (4, 8)}
_UNKNOWN_DISTANCE_BOUNDS = (8, 15000)


# ---------------------------------------------------------------- currency

def _mentions(text: str) -> list[tuple[str, float]]:
    """(currency, amount) pairs written in free text."""
    out = []
    for m in _MONEY.finditer(text or ""):
        tag = m.group("pre") or m.group("post")
        amount = m.group(2) if m.group("pre") else m.group(3)
        code = dict(_SYMBOLS).get(tag, tag)
        if code not in ISO_4217:
            continue
        try:
            value = float(re.sub(r"[,\s]", "", amount) if re.search(r"[,\s]\d{3}\b", amount)
                          else amount.replace(",", "."))
        except ValueError:
            continue
        out.append((code, value))
    return out


def _url_currencies(*urls: str) -> list[str]:
    """Currency a booking/source URL states (?curr=EUR) or implies (its country TLD)."""
    explicit, implied = [], []
    for url in urls:
        if not url:
            continue
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        for key, values in parse_qs(parsed.query).items():
            if key.lower() in {"curr", "currency", "cur", "currencycode"}:
                explicit += [v.upper() for v in values if v.upper() in ISO_4217]
        tld = (parsed.hostname or "").rsplit(".", 1)[-1]
        if tld in _TLD_CURRENCY:
            implied.append(_TLD_CURRENCY[tld])
    return explicit + implied


def _first_unique(values: list[str]) -> str:
    distinct = list(dict.fromkeys(values))
    return distinct[0] if len(distinct) == 1 else ""


def _infer_currency(deal: FlightDeal) -> tuple[str, str]:
    """(currency, evidence) from the deal's own text, or ('', '')."""
    mentions = _mentions(deal.notes)
    tolerance = max(1.0, abs(deal.price) * 0.01)
    same_amount = [c for c, v in mentions if abs(v - deal.price) <= tolerance]
    if _first_unique(same_amount):
        return _first_unique(same_amount), "the amount quoted in notes"
    urls = _url_currencies(deal.booking_url, deal.source_url)
    if urls:
        return urls[0], "booking/source URL"
    if _first_unique([c for c, _ in mentions]):
        return _first_unique([c for c, _ in mentions]), "notes"
    return "", ""


def _plausible_currencies(deal: FlightDeal, places: list, params: SearchParams | None) -> set[str]:
    plausible = set(_GLOBAL_CURRENCIES)
    if params is not None:
        plausible.add(params.currency)
    for place in places:
        if place is not None:
            plausible.add(airports.country_currency(place.country))
    plausible.update(_url_currencies(deal.booking_url, deal.source_url))
    plausible.update(c for c, _ in _mentions(deal.notes))
    return plausible - {""}


# ---------------------------------------------------------------- dates & times

_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})\s*$")
_YMD_SLASH = re.compile(r"^\s*(\d{4})[./](\d{1,2})[./](\d{1,2})\s*$")
_ISO_EXACT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]\d{1,2}:\d{2}")


def _mkdate(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def normalize_date(text: str, today: date | None = None) -> str:
    """ISO 'YYYY-MM-DD' when `text` names one unambiguous date, else `text` unchanged.

    '14 Oct 2026', 'Oct 14' (next occurrence after `today`), '14/10/2026' (the
    day > 12, so day-first is certain) and '2026/10/14' convert; '10/11/2026',
    'mid October' and ranges stay as written.
    """
    s = (text or "").strip()
    if not s or _ISO_EXACT.match(s):
        return s
    m = _ISO_DATETIME.match(s)
    if m:
        return m.group(1)
    m = _YMD_SLASH.match(s)
    if m:
        d = _mkdate(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.isoformat() if d else s
    m = _NUMERIC_DATE.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a == b or a > 12:
            d = _mkdate(y, b, a)
        elif b > 12:
            d = _mkdate(y, a, b)
        else:
            d = None  # 10/11: October 11th or 10 November — leave it
        return d.isoformat() if d else s
    d = _month_name_date(s, today or date.today())
    return d.isoformat() if d else s


_MONTHS = {name: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
     ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
     ("oct", "october"), ("nov", "november"), ("dec", "december")], start=1) for name in names}
_WEEKDAYS = {name: i for i, names in enumerate(
    [("mon", "monday"), ("tue", "tues", "tuesday"), ("wed", "wednesday"), ("thu", "thur", "thurs", "thursday"),
     ("fri", "friday"), ("sat", "saturday"), ("sun", "sunday")]) for name in names}
_MON = r"([a-z]{3,9})\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_WD = r"(?:([a-z]{3,9})\.?,?\s+)?"
_YEAR = r"(?:,?\s+(\d{4}))?"
# The WHOLE string must be one date ("Fri, Oct 2", "14 October 2026"); anything
# else — "after Oct 11", "mid October", "Oct 12-15" — stays as written.
_MONTH_FIRST = re.compile(rf"^{_WD}{_MON}\s+{_DAY}{_YEAR}$")
_DAY_FIRST = re.compile(rf"^{_WD}{_DAY}\s+{_MON}{_YEAR}$")
_MAX_AHEAD_DAYS = 270  # a year-less date further out than ~9 months is a guess, not a fact


def _month_name_date(s: str, today: date) -> date | None:
    t = re.sub(r"\s+", " ", s.strip().lower())
    m = _MONTH_FIRST.match(t)
    if m:
        wd, mon, day, year = m.groups()
    else:
        m = _DAY_FIRST.match(t)
        if not m:
            return None
        wd, day, mon, year = m.groups()
    month = _MONTHS.get(mon)
    if month is None or (wd and wd not in _WEEKDAYS):
        return None
    if year:
        d = _mkdate(int(year), month, int(day))
    else:
        # Next occurrence, allowing a week of slack for deals found just after
        # departure — but never a far-future guess (a July date seen in August
        # is a stale deal, not next July's).
        d = _mkdate(today.year, month, int(day))
        if d and d < today - timedelta(days=7):
            d = _mkdate(today.year + 1, month, int(day))
        if d and (d - today).days > _MAX_AHEAD_DAYS:
            return None
    if d and wd and d.weekday() != _WEEKDAYS[wd]:
        return None  # "Fri, Oct 3" when Oct 3 is a Saturday: something's off
    return d


_TIME = re.compile(r"^\s*(\d{1,2})[:.hH](\d{2})\s*([AaPp]\.?[Mm]\.?)?\s*$")


def normalize_time(text: str) -> str:
    """'6:10' -> '06:10', '6:10 pm' -> '18:10', '18h30' -> '18:30'; anything else unchanged."""
    m = _TIME.match(text or "")
    if not m:
        return (text or "").strip()
    h, mins, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ampm.startswith("p") and h < 12:
        h += 12
    elif ampm.startswith("a") and h == 12:
        h = 0
    if h > 23 or mins > 59:
        return text.strip()
    return f"{h:02d}:{mins:02d}"


# ---------------------------------------------------------------- places

def _place_fields(side: str, text: str, code: str) -> tuple[str, str, list[str], object]:
    """(display text, airport code, flags, resolved place) for one end of the route."""
    flags: list[str] = []
    text = re.sub(r"\s+", " ", text or "").strip()
    code = (code or "").strip().upper()
    by_text = airports.resolve(text)
    if code and not re.fullmatch(r"[A-Z]{3}", code):
        fixed = airports.resolve(code)
        code = fixed.iata if fixed else ""
        if not code:
            flags.append(f"{side}_airport_invalid")
    by_code = airports.airport(code) if code else None
    if code and by_code is None:
        flags.append(f"{side}_airport_unknown")
    if not code and by_text is not None:
        code = by_text.iata  # a real airport or metro (LON); '' for pseudo-metros like IST/SAW

    if by_text is not None and by_code is not None and not airports.contains(by_text, code) \
            and airports._key(by_text.city) != airports._key(by_code.city):
        flags.append(f"{side}_mismatch")
        return text, code, flags, by_code  # trust the code, keep the text for a human to judge
    if by_text is not None:
        text = by_text.city
    elif by_code is not None and (not text or text.upper() == code):
        text = by_code.city
    return text, code, flags, by_code or by_text


# ---------------------------------------------------------------- price

def _price_flags(deal: FlightDeal, places: list, params: SearchParams | None) -> list[str]:
    usd = _convert_currency(deal.price, deal.currency, "USD")
    if usd is None:
        return []
    paying = (params.passengers.adults + params.passengers.children) if params else 1
    per_person = usd / max(paying, 1)
    round_trip = (params.trip_type == "round_trip") if params else bool(deal.return_date.strip())
    cabin = params.cabin_class if params else "economy"
    distance = airports.distance_km(*places) if all(places) else None
    if distance is None:
        low, high = _UNKNOWN_DISTANCE_BOUNDS
    else:
        _, low, high = next(band for band in _PRICE_BANDS if distance <= band[0])
        lo_f, hi_f = _CABIN_FACTOR.get(cabin, (1, 1))
        low, high = low * lo_f, high * hi_f
        if round_trip:
            low, high = low * 1.5, high * 2
    if per_person < low:
        return ["price_too_low"]
    if per_person > high:
        return ["price_too_high"]
    return []


# ---------------------------------------------------------------- entry points

def normalize_deal(deal: FlightDeal, params: SearchParams | None = None,
                   today: date | None = None) -> tuple[FlightDeal, list[str]]:
    """Clean one deal; returns (a copy with normalized fields + quality_flags, flags).

    `params` is the run's criteria (None for legacy rows); `today` anchors
    year-less dates ('Oct 14') — pass the row's found_at date when repairing.
    """
    flags: list[str] = []
    update: dict = {}

    # Flight numbers first: their prefix can name the airline.
    flights, bad_flights = airlines.normalize_flight_numbers(deal.flight_numbers)
    if bad_flights:
        flags.append("flight_number_invalid")
    update["flight_numbers"] = flights

    raw_code = (deal.airline_code or "").strip().upper()
    if raw_code and not re.fullmatch(r"[A-Z0-9]{2}", raw_code):
        flags.append("airline_code_invalid")
        raw_code = ""
    name, code, matches = airlines.resolve(deal.airline, raw_code, flights)
    update["airline"], update["airline_code"] = name, code
    if not name and not code:
        flags.append("airline_missing")
    elif name != airlines.MULTIPLE and (not all(matches) if matches else not airlines.by_code(code)):
        flags.append("airline_unrecognized")
    prefix = airlines.by_code(airlines.flight_prefix(flights))
    if prefix and matches and all(matches) and prefix.group not in {m.group for m in matches}:
        flags.append("flight_airline_mismatch")

    places = []
    for side in ("origin", "destination"):
        text, code, side_flags, place = _place_fields(side, getattr(deal, side),
                                                      getattr(deal, f"{side}_airport"))
        update[side], update[f"{side}_airport"] = text, code
        flags += side_flags
        places.append(place)
    if not (update["origin"] or update["origin_airport"]) and \
            not (update["destination"] or update["destination_airport"]):
        flags.append("no_route")

    currency = deal.currency
    probe = deal.model_copy(update=update)
    if currency not in ISO_4217 or currency not in _plausible_currencies(probe, places, params):
        inferred, evidence = _infer_currency(probe)
        if inferred and inferred != currency:
            update["currency"] = inferred
            update["notes"] = f"{deal.notes} [currency was '{currency}'; {inferred} inferred from {evidence}]".strip()
            flags.append("currency_inferred")
        elif currency not in ISO_4217:
            flags.append("currency_invalid")
        else:
            flags.append("currency_unusual")

    for field in ("depart_date", "return_date"):
        update[field] = normalize_date(getattr(deal, field), today)
    for field in ("depart_time", "arrive_time", "return_depart_time"):
        update[field] = normalize_time(getattr(deal, field))
    out_date, back_date = update["depart_date"], update["return_date"]
    if not out_date:
        flags.append("depart_date_missing")
    elif not _ISO_EXACT.match(out_date):
        flags.append("depart_date_inexact")
    if _ISO_EXACT.match(out_date) and _ISO_EXACT.match(back_date) and back_date < out_date:
        flags.append("return_before_depart")
    if deal.stops < 0:
        update["stops"] = 0

    if deal.price is None or not math.isfinite(deal.price) or deal.price <= 0:
        flags.append("no_price")
    else:
        flags += _price_flags(deal.model_copy(update=update), places, params)

    previous = getattr(deal, "quality_flags", None) or []
    flags = sorted(set(flags) | (set(previous) & STICKY))
    update["quality_flags"] = flags
    return deal.model_copy(update=update), flags


def is_fatal(flags: list[str]) -> bool:
    return bool(FATAL.intersection(flags))


def clean(deal: FlightDeal, params: SearchParams | None = None,
          today: date | None = None) -> FlightDeal | None:
    """The normalized deal, or None (logged) when it has a fatal problem. A bug in
    the cleanup itself keeps the deal as extracted — it must never lose results."""
    try:
        cleaned, flags = normalize_deal(deal, params, today)
    except Exception:
        logger.exception("normalize_deal failed; keeping the deal as extracted")
        return deal
    if is_fatal(flags):
        logger.info("dropping deal %s -> %s (%s %s %s): %s", deal.origin, deal.destination,
                    deal.airline, deal.price, deal.currency, ", ".join(sorted(FATAL.intersection(flags))))
        return None
    return cleaned


def normalize_deals(deals: list[FlightDeal], params: SearchParams | None = None,
                    today: date | None = None) -> list[FlightDeal]:
    """Normalize a batch, dropping (and logging) deals with fatal problems."""
    return [d for d in (clean(deal, params, today) for deal in deals) if d is not None]
