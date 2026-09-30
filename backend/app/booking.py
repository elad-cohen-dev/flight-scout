"""Build pre-filled booking links for a deal.

Contract (used by storage when returning deals, so legacy rows get links too):

    build_links(deal, params) -> list[BookingLink]

Pure (no network), never raises, ordered best-first. `prefilled` on each link
lists only what that URL really carries: 'route', 'dates', 'passengers',
'cabin', 'bags', 'stops'.

URL formats (checked against the live sites, 2026-09):
  Google Flights  /travel/flights?q=<natural language>  (route/dates/pax/cabin/stops parsed by Google)
  Skyscanner      /transport/flights/{from}/{to}/{yymmdd}[/{yymmdd}]/?adultsv2&childrenv2=<ages|>&cabinclass&rtn&preferdirects
                  (whole-month view: yymm instead of yymmdd)
  Kayak/Momondo   /flights|flight-search/{FROM}-{TO}/{date}[/{date}][/{cabin}]/{n}adults[/children-8-1L]?fs=stops=0;cfc=1;bfc=1
                  (cfc/bfc = carry-on/checked bag counts; Kayak re-prices fares with those bag fees)
  Ryanair         /gb/en/trip/flights/select?adults&teens&children&infants&dateOut&dateIn&isReturn&originIata&destinationIata
  Wizz Air        /en-gb/booking/select-flight/{FROM}/{TO}/{out}/{ret|null}/{adults}/{children}/{infants}/null
  easyJet         /deeplink?lang=EN&dep&dest&dd&rd&apax&cpax&ipax&isOneWay=on|off
Other carriers have no documented search deep link, so they get their booking home page.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import quote, urlencode, urlparse

from .models import BookingLink, FlightDeal, Passengers, SearchParams

# Assumed ages: sites that price by age need one, and SearchParams only records
# the child/infant bands (2-11, under 2).
CHILD_AGE = 8
INFANT_AGE = 1

# City name -> IATA code (metro code where one exists). Keys are lowercase.
CITY_CODES: dict[str, str] = {
    "tel aviv": "TLV", "tel-aviv": "TLV", "tel aviv-yafo": "TLV", "ben gurion": "TLV",
    "eilat": "ETM", "ramon": "ETM", "haifa": "HFA",
    "london": "LON", "paris": "PAR", "rome": "ROM", "milan": "MIL", "new york": "NYC", "new york city": "NYC",
    "moscow": "MOW", "stockholm": "STO", "washington": "WAS", "chicago": "CHI", "tokyo": "TYO",
    "bucharest": "BUH", "sao paulo": "SAO", "buenos aires": "BUE", "toronto": "YTO", "montreal": "YMQ",
    "seoul": "SEL", "beijing": "BJS", "osaka": "OSA",
    "prague": "PRG", "athens": "ATH", "larnaca": "LCA", "paphos": "PFO", "budapest": "BUD", "vienna": "VIE",
    "berlin": "BER", "munich": "MUC", "frankfurt": "FRA", "hamburg": "HAM", "dusseldorf": "DUS",
    "zurich": "ZRH", "geneva": "GVA", "amsterdam": "AMS", "brussels": "BRU", "barcelona": "BCN",
    "madrid": "MAD", "lisbon": "LIS", "porto": "OPO", "nice": "NCE", "marseille": "MRS", "lyon": "LYS",
    "naples": "NAP", "venice": "VCE", "florence": "FLR", "bologna": "BLQ", "catania": "CTA",
    "istanbul": "IST", "antalya": "AYT", "dubai": "DXB", "abu dhabi": "AUH", "warsaw": "WAW",
    "krakow": "KRK", "sofia": "SOF", "varna": "VAR", "burgas": "BOJ", "belgrade": "BEG",
    "tbilisi": "TBS", "batumi": "BUS", "yerevan": "EVN", "baku": "GYD", "vilnius": "VNO", "riga": "RIX",
    "tallinn": "TLL", "copenhagen": "CPH", "oslo": "OSL", "helsinki": "HEL", "dublin": "DUB",
    "thessaloniki": "SKG", "rhodes": "RHO", "heraklion": "HER", "crete": "HER", "corfu": "CFU",
    "santorini": "JTR", "mykonos": "JMK", "bangkok": "BKK", "phuket": "HKT", "delhi": "DEL",
    "mumbai": "BOM", "marrakech": "RAK", "cairo": "CAI", "sharm el sheikh": "SSH", "tirana": "TIA",
    "podgorica": "TGD", "tivat": "TIV", "dubrovnik": "DBV", "split": "SPU", "zagreb": "ZAG",
    "ljubljana": "LJU", "bratislava": "BTS", "katowice": "KTW", "gdansk": "GDN", "cluj": "CLJ",
    "iasi": "IAS", "chisinau": "RMO", "malta": "MLA", "valletta": "MLA", "palma": "PMI", "mallorca": "PMI",
    "ibiza": "IBZ", "malaga": "AGP", "seville": "SVQ", "valencia": "VLC", "manchester": "MAN",
    "edinburgh": "EDI", "los angeles": "LAX", "miami": "MIA", "san francisco": "SFO", "boston": "BOS",
}

# Metro (multi-airport) codes: fine for meta-search, rejected by airline booking engines.
METRO_CODES = {"LON", "PAR", "ROM", "MIL", "NYC", "MOW", "STO", "WAS", "CHI", "TYO", "BUH", "SAO",
               "BUE", "YTO", "YMQ", "SEL", "BJS", "OSA", "REK", "JKT", "RIO", "YEA", "DTT", "QDF", "QSF"}

# Skyscanner's own ids for metro areas (its paths don't take the IATA metro code).
SKYSCANNER_METRO = {"LON": "lond", "PAR": "pari", "ROM": "rome", "MIL": "mila", "NYC": "nyca",
                    "MOW": "mosc", "STO": "stoc", "WAS": "wasa", "CHI": "chia", "TYO": "tyoa"}

MONTHS = {m: i for i, names in enumerate([
    (), ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
    ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
    ("oct", "october"), ("nov", "november"), ("dec", "december")]) for m in names}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

CABIN_WORDS = {"economy": "economy", "premium_economy": "premium economy", "business": "business class", "first": "first class"}
SKYSCANNER_CABIN = {"economy": "economy", "premium_economy": "premiumeconomy", "business": "business", "first": "first"}
KAYAK_CABIN = {"economy": "", "premium_economy": "premium", "business": "business", "first": "first"}


# ---------------------------------------------------------------- parsing

def _parse_date(text: str, today: date | None = None) -> date | None:
    """An exact calendar date from free text, or None. Accepts ISO dates and
    '12 Oct [2026]' / 'Oct 12[, 2026]'. A missing year means the next
    occurrence (a week of slack for deals found recently). Text naming more
    than one date is a range, not an exact date."""
    if not text:
        return None
    t = text.strip().lower()
    today = today or date.today()
    iso = re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if iso:
        if len(set(iso)) > 1:
            return None
        y, m, d = map(int, iso[0])
        return _mkdate(y, m, d)
    hits = [(int(d), MONTHS[mon[:3]], y)
            for d, mon, y in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_RE}\.?,?(?:\s+(\d{{4}}))?\b", t)]
    hits += [(int(d), MONTHS[mon[:3]], y)
             for mon, d, y in re.findall(rf"\b{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", t)]
    if len(set(hits)) != 1 or re.search(r"\d\s*[-–]\s*\d", t):
        return None
    d, m, y = hits[0]
    if y:
        return _mkdate(int(y), m, d)
    guess = _mkdate(today.year, m, d)
    if guess and guess < today - timedelta(days=7):
        guess = _mkdate(today.year + 1, m, d)
    return guess


def _mkdate(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _parse_month(text: str, today: date | None = None) -> tuple[int, int] | None:
    """(year, month) for month-level text like 'mid October' or '2026-10'."""
    if not text:
        return None
    t = text.strip().lower()
    today = today or date.today()
    m = re.search(r"\b(\d{4})-(\d{1,2})\b", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    m = re.search(rf"\b{_MONTH_RE}\b(?:\s+(\d{{4}}))?", t)
    if not m:
        return None
    mon = MONTHS[m.group(1)[:3]]
    if m.group(2):
        return int(m.group(2)), mon
    return (today.year if mon >= today.month else today.year + 1), mon


def _airport(code_field: str, name_field: str) -> str:
    """IATA code for one end of the route, or '' when it can't be resolved."""
    for raw in (code_field, name_field):
        s = (raw or "").strip()
        if not s:
            continue
        if re.fullmatch(r"[A-Za-z]{3}", s):
            return s.upper()
        m = re.search(r"\(([A-Z]{3})\)", s)  # "Tel Aviv (TLV)"
        if m:
            return m.group(1)
        key = re.sub(r"\s+", " ", s.lower().split(",")[0]).strip()
        if key in CITY_CODES:
            return CITY_CODES[key]
    return ""


# ---------------------------------------------------------------- trip model

class _Trip:
    """Everything a URL builder needs, normalized once."""

    def __init__(self, deal: FlightDeal, params: SearchParams | None):
        if isinstance(params, dict):
            params = SearchParams.model_validate(params)
        p = params or SearchParams()
        self.pax: Passengers = p.passengers
        self.cabin = p.cabin_class
        self.max_stops = p.max_stops
        self.cabin_bag = p.cabin_bag
        self.checked_bag = p.checked_bag
        self.currency = p.currency if params else ""
        self.origin = _airport(deal.origin_airport, deal.origin)
        self.dest = _airport(deal.destination_airport, deal.destination)
        self.is_return = bool((deal.return_date or "").strip()) and p.trip_type == "round_trip"
        self.out = _parse_date(deal.depart_date)
        self.back = _parse_date(deal.return_date) if self.is_return else None
        if self.out and self.back and self.back < self.out:
            self.back = None
        # Dated deep links only when every leg has an exact date.
        self.dated = bool(self.out) and (not self.is_return or bool(self.back))
        self.month = None if self.dated else (_parse_month(deal.depart_date) or
                                              (self.out and (self.out.year, self.out.month)))
        self.airport_codes = bool(self.origin and self.dest) and not (
            {self.origin, self.dest} & METRO_CODES)

    @property
    def has_route(self) -> bool:
        return bool(self.origin and self.dest) and self.origin != self.dest

    def base_prefilled(self) -> list[str]:
        return ["route", "dates"] if self.dated else ["route"]


def _pax_words(p: Passengers) -> str:
    parts = [f"{p.adults} adult{'s' if p.adults != 1 else ''}"]
    if p.children:
        parts.append(f"{p.children} child{'ren' if p.children != 1 else ''}")
    if p.infants:
        # plain "N infant(s)": adding "on lap" makes Google's ?q= parser give up
        parts.append(f"{p.infants} infant{'s' if p.infants != 1 else ''}")
    return " ".join(parts)


# ---------------------------------------------------------------- meta-search

def _google(t: _Trip) -> BookingLink:
    # Google's ?q= parser is picky (verified in a real browser, 2026-09-30):
    # it only resolves the route as "to X from Y" ("from Y to X" falls back to
    # the home page), and it rejects queries that combine a passenger count
    # with a cabin class. So: passengers only when not 1 adult, and cabin only
    # when passengers were left out; whatever is dropped isn't marked prefilled.
    q = f"Flights to {t.dest} from {t.origin}"
    if t.dated:
        q += f" on {t.out.isoformat()}"
        q += f" through {t.back.isoformat()}" if t.back else " oneway"
    else:
        if t.month:
            q += f" in {date(t.month[0], t.month[1], 1):%B %Y}"
        if not t.is_return:
            q += " oneway"
    prefilled = t.base_prefilled()
    solo = t.pax.adults == 1 and not t.pax.children and not t.pax.infants
    if not solo:
        q += f" for {_pax_words(t.pax)}"
        prefilled.append("passengers")
    else:
        prefilled.append("passengers")  # Google's default is exactly 1 adult
        if t.cabin != "economy":
            q += f" {CABIN_WORDS[t.cabin]}"
    if solo or t.cabin == "economy":
        prefilled.append("cabin")
    if t.max_stops == 0:
        q += " nonstop"
        prefilled.append("stops")
    query = {"q": q, "hl": "en"}
    if t.currency:
        query["curr"] = t.currency
    return BookingLink(label="Google Flights", kind="google_flights", prefilled=prefilled,
                       url="https://www.google.com/travel/flights?" + urlencode(query, quote_via=quote))


def _skyscanner(t: _Trip) -> BookingLink:
    def place(code: str) -> str:
        return SKYSCANNER_METRO.get(code, code.lower())

    path = f"https://www.skyscanner.net/transport/flights/{place(t.origin)}/{place(t.dest)}/"
    prefilled = ["route"]
    if t.dated:
        path += f"{t.out:%y%m%d}/" + (f"{t.back:%y%m%d}/" if t.back else "")
        prefilled.append("dates")
    elif t.month:  # whole-month calendar view
        ym = f"{t.month[0] % 100:02d}{t.month[1]:02d}"
        path += f"{ym}/" + (f"{ym}/" if t.is_return else "")
    ages = [str(CHILD_AGE)] * t.pax.children + [str(INFANT_AGE)] * t.pax.infants
    query = {
        "adultsv2": t.pax.adults,
        "childrenv2": "|".join(ages),
        "cabinclass": SKYSCANNER_CABIN[t.cabin],
        "rtn": 1 if t.is_return else 0,
        "preferdirects": "true" if t.max_stops == 0 else "false",
    }
    prefilled += ["passengers", "cabin"] + (["stops"] if t.max_stops == 0 else [])
    return BookingLink(label="Skyscanner", kind="skyscanner", prefilled=prefilled,
                       url=path + "?" + urlencode(query, safe="|"))


def _kayak_style(t: _Trip, host: str, section: str, label: str, kind: str) -> BookingLink | None:
    if not t.dated:  # no month view on these sites
        return None
    path = f"https://{host}/{section}/{t.origin}-{t.dest}/{t.out.isoformat()}"
    if t.back:
        path += f"/{t.back.isoformat()}"
    if KAYAK_CABIN[t.cabin]:
        path += f"/{KAYAK_CABIN[t.cabin]}"
    path += f"/{t.pax.adults}adults"
    kids = [str(CHILD_AGE)] * t.pax.children + [f"{INFANT_AGE}L"] * t.pax.infants
    if kids:
        path += "/children-" + "-".join(kids)
    prefilled = ["route", "dates", "passengers", "cabin"]
    fs = []
    if t.max_stops == 0:
        fs.append("stops=0")
        prefilled.append("stops")
    if t.cabin_bag or t.checked_bag:
        fs += (["cfc=1"] if t.cabin_bag else []) + (["bfc=1"] if t.checked_bag else [])
        prefilled.append("bags")
    url = path + "?sort=price_a" + (f"&fs={';'.join(fs)}" if fs else "")
    return BookingLink(label=label, kind=kind, prefilled=prefilled, url=url)


# ---------------------------------------------------------------- airlines

def _ryanair(t: _Trip) -> str | None:
    q = {
        "adults": t.pax.adults, "teens": 0, "children": t.pax.children, "infants": t.pax.infants,
        "dateOut": t.out.isoformat(), "dateIn": t.back.isoformat() if t.back else "",
        "isConnectedFlight": "false", "discount": 0, "isReturn": "true" if t.back else "false",
        "promoCode": "", "originIata": t.origin, "destinationIata": t.dest,
    }
    return "https://www.ryanair.com/gb/en/trip/flights/select?" + urlencode(q)


def _wizz(t: _Trip) -> str | None:
    back = t.back.isoformat() if t.back else "null"
    return (f"https://www.wizzair.com/en-gb/booking/select-flight/{t.origin}/{t.dest}/"
            f"{t.out.isoformat()}/{back}/{t.pax.adults}/{t.pax.children}/{t.pax.infants}/null")


def _easyjet(t: _Trip) -> str | None:
    q = {
        "lang": "EN", "dep": t.origin, "dest": t.dest, "dd": t.out.isoformat(),
        "rd": t.back.isoformat() if t.back else "", "apax": t.pax.adults, "cpax": t.pax.children,
        "ipax": t.pax.infants, "isOneWay": "off" if t.back else "on",
    }
    return "https://www.easyjet.com/deeplink?" + urlencode(q)


# (IATA codes, name aliases, display label, booking home, deep-link builder, economy-only)
AIRLINES = [
    (("FR", "RK", "AL"), ("ryanair",), "Ryanair", "https://www.ryanair.com/gb/en", _ryanair, True),
    (("W6", "W4", "W9", "5W"), ("wizz", "wizzair", "wizz air"), "Wizz Air", "https://www.wizzair.com/en-gb", _wizz, True),
    (("U2", "EC", "DS"), ("easyjet",), "easyJet", "https://www.easyjet.com/en", _easyjet, True),
    (("LY",), ("el al", "elal", "el-al"), "El Al", "https://www.elal.com/eng", None, False),
    (("6H",), ("israir",), "Israir", "https://www.israir.co.il/en", None, False),
    (("IZ",), ("arkia",), "Arkia", "https://www.arkia.com/en", None, False),
    (("BZ",), ("blue bird", "bluebird"), "Blue Bird", "https://www.bluebirdair.com", None, False),
    (("A3", "OA"), ("aegean", "olympic"), "Aegean", "https://en.aegeanair.com", None, False),
    (("LH",), ("lufthansa",), "Lufthansa", "https://www.lufthansa.com", None, False),
    (("LX",), ("swiss",), "SWISS", "https://www.swiss.com", None, False),
    (("OS",), ("austrian",), "Austrian", "https://www.austrian.com", None, False),
    (("SN",), ("brussels airlines",), "Brussels Airlines", "https://www.brusselsairlines.com", None, False),
    (("EW",), ("eurowings",), "Eurowings", "https://www.eurowings.com", None, False),
    (("TK",), ("turkish",), "Turkish Airlines", "https://www.turkishairlines.com", None, False),
    (("PC",), ("pegasus",), "Pegasus", "https://www.flypgs.com/en", None, False),
    (("QS",), ("smartwings", "smart wings"), "Smartwings", "https://www.smartwings.com/en", None, False),
    (("OK",), ("czech airlines", "csa"), "Czech Airlines", "https://www.csa.cz/en", None, False),
]


def _match_airline(deal: FlightDeal):
    code = (deal.airline_code or "").strip().upper()
    if not code:
        m = re.match(r"\s*([A-Z0-9]{2})\s?\d{1,4}\b", deal.flight_numbers or "")
        code = m.group(1) if m else ""
    name = re.sub(r"[^a-z ]", " ", (deal.airline or "").lower())
    name = re.sub(r"\s+", " ", name).strip()
    for entry in AIRLINES:
        if code and code in entry[0]:
            return entry
    for entry in AIRLINES:
        if any(re.search(rf"\b{re.escape(alias)}\b", name) for alias in entry[1]):
            return entry
    return None


def _airline_link(deal: FlightDeal, t: _Trip) -> BookingLink | None:
    entry = _match_airline(deal)
    if not entry:
        return None
    _, _, label, home, builder, economy_only = entry
    if builder and t.dated and t.airport_codes:
        url = builder(t)
        if url:
            prefilled = ["route", "dates", "passengers"]
            if economy_only and t.cabin == "economy":
                prefilled.append("cabin")
            return BookingLink(label=label, url=url, kind="airline", prefilled=prefilled)
    return BookingLink(label=f"{label} site", url=home, kind="airline", prefilled=[])


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_specific(url: str) -> bool:
    """True when a URL points past a site's home page (a path or query)."""
    u = urlparse(url)
    return bool(u.query) or len(u.path.strip("/")) > 5


# ---------------------------------------------------------------- entry point

def build_links(deal: FlightDeal, params: SearchParams | None) -> list[BookingLink]:
    try:
        return _build(deal, params)
    except Exception:  # never break a deals listing over a link
        try:
            url = (getattr(deal, "booking_url", "") or "").strip()
            if url.startswith(("http://", "https://")):
                return [BookingLink(label=_domain(url) or "Book", url=url, kind="other")]
        except Exception:
            pass
        return []


def _build(deal: FlightDeal, params: SearchParams | None) -> list[BookingLink]:
    t = _Trip(deal, params)
    top: list[BookingLink] = []
    rest: list[BookingLink] = []

    airline = _airline_link(deal, t)
    if airline and airline.prefilled:
        top.append(airline)

    if t.has_route:
        top.append(_google(t))

    agent_url = (deal.booking_url or "").strip()
    agent = None
    if agent_url.startswith(("http://", "https://")):
        agent = BookingLink(label=_domain(agent_url) or "Book", url=agent_url, kind="other")
        (top if _is_specific(agent_url) else rest).append(agent)

    if t.has_route:
        top.append(_skyscanner(t))
        for link in (_kayak_style(t, "www.kayak.com", "flights", "Kayak", "kayak"),
                     _kayak_style(t, "www.momondo.com", "flight-search", "Momondo", "momondo")):
            if link:
                top.append(link)

    if airline and not airline.prefilled:
        # A specific agent URL on the airline's own site beats its home page.
        if not (agent and _is_specific(agent.url) and _domain(agent.url) == _domain(airline.url)):
            rest.insert(0, airline)

    seen: set[str] = set()
    out: list[BookingLink] = []
    for link in top + rest:
        if link.url not in seen:
            seen.add(link.url)
            out.append(link)
    return out
