"""LangGraph agent: search the live web for cheap flights, then extract structured deals.

Graph shape:

    START -> research -> extract -> END

- ``research`` calls the model with its provider's server-side web tools bound
  (web search, plus web fetch on Anthropic), so the searching/fetching loop runs
  on the provider's side and the node gets back prose findings.
- ``extract`` runs a second structured-output pass that turns those findings into a
  validated ``FlightDealList``, then applies deterministic post-filters
  (``postprocess``): stops, price ceiling, date window, trip length, past dates,
  de-duplication and price sorting. The prompts ask; the filters guarantee.

Every search is driven by a ``SearchParams`` (trip type, party, cabin, stops,
bags, date window, budget, currency, nearby airports). Prices are always the
TOTAL for the whole party including the requested bags.

Works with either Anthropic (ANTHROPIC_API_KEY) or OpenAI (OPENAI_API_KEY); the
provider is auto-detected from whichever key is set.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Callable, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from .currency import convert as _convert_currency
from .models import FlightDeal, FlightDealList, SearchParams
from .normalize import clean as _normalize

load_dotenv()

logger = logging.getLogger(__name__)

# Progress callback: receives {"kind": "phase"|"delta"|"search", "text": str}.
# "delta" is a streamed chunk of the research pass's prose; "search" fires when
# the model issues a web-search (or page-fetch) call. Used by the API to
# live-stream agent activity to the UI; None (the watcher/CLI case) disables it.
OnEvent = Callable[[dict], None]


class SearchCancelled(Exception):
    """Raised BY the on_event callback to abort a run cooperatively.

    Emission points double as cancellation points: streaming deltas arrive
    every ~100ms during research, so a cancel lands almost immediately there;
    the phase emits between nodes (and before saving) bound the worst case.
    Everything that catches broad exceptions around agent work must let this
    one through."""

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    # mini-tier is plenty for search-and-extract work and far cheaper;
    # override with FLIGHT_SCOUT_MODEL if you want a bigger model.
    "openai": "gpt-5.4-mini",
}

# Server-side web tools: executed on the provider's infrastructure, results come
# back inside the same model response — no client tool loop needed. Budgets are
# per search; flexible searches (regions, lists, nearby airports) get more.
SEARCH_USES = 10
FLEXIBLE_SEARCH_USES = 14
FETCH_USES = 5  # opening result/airline pages to confirm exact dated fares


def _web_tools(provider: str, flexible: bool) -> list[dict]:
    if provider == "anthropic":
        return [
            {"type": "web_search_20260209", "name": "web_search",
             "max_uses": FLEXIBLE_SEARCH_USES if flexible else SEARCH_USES},
            # Only fetches URLs already in the conversation (i.e. search hits).
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": FETCH_USES},
        ]
    return [{"type": "web_search"}]


def _provider() -> str:
    forced = os.environ.get("FLIGHT_SCOUT_PROVIDER")
    if forced:
        return forced.lower()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "No model credentials found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY "
        "in backend/.env (see .env.example)."
    )


def _make_llm(flexible: bool = False):
    """Return (chat model, web tool dicts) for the active provider."""
    provider = _provider()
    model = os.environ.get("FLIGHT_SCOUT_MODEL", DEFAULT_MODELS[provider])
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, max_tokens=16000), _web_tools(provider, flexible)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        # Built-in web search requires OpenAI's Responses API.
        return ChatOpenAI(model=model, use_responses_api=True), _web_tools(provider, flexible)
    raise RuntimeError(f"Unknown FLIGHT_SCOUT_PROVIDER: {provider}")


# --- prompts -----------------------------------------------------------------

RESEARCH_PROMPT = """You are a meticulous flight-fare researcher working like a flight search engine.
Today is {today}. Never report flights departing before today.

SEARCH
- From: {origin}
- To: {destination}
- Trip: {trip}
- Dates: {dates_hint}{date_window}
- Travellers: {party}
- Cabin: {cabin}
- Stops: {stops}{airports_clause}{budget_clause}{flexible_clause}{baggage_clause}

HOW TO RESEARCH
1. Prefer EXACT dated fares from flight search engines and airline sites: Google Flights,
   Skyscanner, Kayak, Momondo, and the airlines' own booking/fare-calendar/low-fare pages.
   Deal blogs (Secret Flying, Fly4Free...) are leads only — confirm them against a dated fare.
   Write natural queries (route + month + airline/site name); avoid narrow site:/quoted
   queries, which mostly return nothing.
2. Open (fetch) the most promising result or airline pages to read the actual fare for
   specific dates, rather than trusting a snippet. "From X" / "fares starting at" teasers
   are NOT a fare for these dates — use them only when nothing better exists, labelled as such.
   Your tool budget is limited ({budget}): once you have a few solid options, stop searching and
   write the report. A report with clearly-labelled estimates beats no report.
3. For each deal identify the real airports (IATA codes, e.g. LTN not "London"), the airline
   and its IATA code, flight numbers, and local departure/arrival times when the page shows them.
   Never invent flight numbers, times or prices — leave them out when unknown.
4. Prices must be the TOTAL for the whole party ({party}) in {cabin}, including the bags
   required above. Google Flights, Skyscanner, Kayak, Trip.com and airline pages quote PER
   PERSON: work out the per-adult price first, then multiply by the travellers (children
   pay the child fare if shown, else the adult fare; lap infants ~10% of the adult fare).{trip_math}
   Only report dates a source actually shows — never make up a return date.
5. Quote prices in {currency} when the source offers it; otherwise give the original amount
   with its currency clearly named (no conversion needed).
6. Before finishing, re-check every deal against the criteria above (dates, stops, cabin,
   trip type, party, budget) and drop the ones that don't fit.
7. ALWAYS deliver a report. When a fare can't be verified for exact dates, still report the
   best matching options you found (route-page examples, calendar fares, teasers) with the
   right confidence label and your best-estimate bag fees marked '~'. Never stop to ask
   the user a question or offer a second pass — this report is final.

REPORT at least 3 distinct options when possible (different dates, airlines or airports),
cheapest first, one block per deal:

DEAL <n>
route: <origin IATA> -> <destination IATA> (<cities>)
airline: <name> (<IATA code>), flights <numbers or "unknown">
outbound: <YYYY-MM-DD> <HH:MM> -> <HH:MM>, <duration>, <direct | N stop(s) via XXX>
return: <YYYY-MM-DD> <HH:MM> -> <HH:MM>, <stops>   (round trips only)
price: per adult <fare> + <bags> = <per-adult amount>; x {travellers} travellers = TOTAL <amount> <currency>
confidence: verified (exact fare seen for these dates on <site>) | estimated (computed or approximate) | from-price teaser
book: <URL where this fare can be booked or searched>
source: <URL of the page the fare came from>
caveats: <fare class, bag rules, deal expiry, anything relevant>"""

EXTRACT_PROMPT = """Below are research notes about flight deals from {origin} to {destination}.
Today is {today}. The search was: {trip}, {party}, {cabin} cabin.

Extract every distinct priced deal into the structured format:
- price: the TOTAL for the whole party ({party}) including any bags the notes add. When the
  notes state a bag-inclusive total in ANY phrasing ('fare $79 + bag $25 = $104', 'estimated
  total ~CHF 260', 'with bag: ~€150') use that final total, never the pre-baggage fare. If the
  notes only give a per-person price, multiply by the number of paying travellers.
  Put the breakdown in notes.
- price_per_person: the price for one adult on the same basis, when stated or computable.
- currency: ISO 4217 code (USD, EUR, ILS, GBP...) — never a symbol. Keep the source currency.
- origin / destination: city names; origin_airport / destination_airport: IATA airport codes.
- depart_date / return_date: YYYY-MM-DD whenever a specific date is known (infer the year
  from today's date: the next future occurrence); otherwise the text as written.
  return_date stays empty for one-way fares.
- depart_time / arrive_time (outbound) and return_depart_time: local HH:MM, only if stated.
- flight_numbers (e.g. 'W6 2327 / W6 2328') and airline_code (IATA, e.g. 'W6'): only if stated.
- stops: number of stops on the outbound (0 = direct).
- price_confidence: 'verified' only when the notes say the exact fare was seen for those
  dates; 'from_price' for "from X"/"starting at" teasers; otherwise 'estimated'.
- booking_links: leave empty (built later).
If a field is unknown, use an empty string (or 0 for stops). Do not invent values.
Skip anything that is not an actual priced flight.

Research notes:
{notes}"""


DIRECT_ONLY_CLAUSE = (
    "direct (non-stop) flights ONLY — skip every itinerary with a connection or "
    "stopover, even if it is cheaper"
)

# Low-cost "from" fares usually cover only a small personal item; the advertised
# price is not what the trip costs. When bags are requested, the agent must
# report bag-inclusive totals — merely flagging "bag fees may apply" is a
# failure mode we've observed, hence the explicit format and estimate mandate.
CABIN_BAG_CLAUSE = (
    "\nBAGGAGE REQUIREMENT (critical): every traveller brings a 10kg cabin trolley. "
    "Every price you report MUST be the trolley-inclusive total. When a fare covers "
    "only a small personal item, look up that airline's cabin-bag/priority fee for "
    "this route (search '<airline> cabin bag fee' if needed) and ADD it (per traveller, "
    "per direction). If only an estimate is possible, add your best estimate marked '~'. "
    "Never report a fare without the trolley added. Give each deal's price as a "
    "breakdown: 'fare X + trolley Y = TOTAL Z'."
)
CHECKED_BAG_CLAUSE = (
    "\nBAGGAGE REQUIREMENT (critical): every traveller checks a 20kg+ bag (both "
    "directions on round trips). Every price you report MUST be the bag-inclusive "
    "total. When the fare excludes it, look up that airline's checked-baggage fee "
    "for this route (search '<airline> checked baggage fee' if needed) and ADD it. "
    "If only an estimate is possible, add your best estimate marked '~'. Never "
    "report a fare without the bag added. Give each deal's price as a breakdown: "
    "'fare X + 20kg bag Y = TOTAL Z'."
)

CABIN_NAMES = {
    "economy": "economy",
    "premium_economy": "premium economy",
    "business": "business class",
    "first": "first class",
}

# Destinations that are clearly not a single place: the research pass must
# compare several concrete airports.
_FLEXIBLE_WORDS = re.compile(
    r"\b(anywhere|everywhere|europe|asia|africa|america|caribbean|"
    r"balkans|scandinavia|mediterranean|islands?|beach|region|country)\b",
    re.IGNORECASE,
)


def _baggage_clause(cabin_bag: bool, checked_bag: bool) -> str:
    return (CABIN_BAG_CLAUSE if cabin_bag else "") + (CHECKED_BAG_CLAUSE if checked_bag else "")


def is_flexible(place: str) -> bool:
    """True for comma lists, regions and 'anywhere' — anything that needs several airports."""
    return "," in place or " or " in place.lower() or bool(_FLEXIBLE_WORDS.search(place))


def describe_party(p: SearchParams) -> str:
    """'1 adult', '2 adults + 1 child + 1 infant (on lap)'."""
    pax = p.passengers
    parts = [f"{pax.adults} adult{'s' if pax.adults != 1 else ''}"]
    if pax.children:
        parts.append(f"{pax.children} child{'ren' if pax.children != 1 else ''} (2-11)")
    if pax.infants:
        parts.append(f"{pax.infants} infant{'s' if pax.infants != 1 else ''} (under 2, on lap)")
    return " + ".join(parts)


def _describe_stops(max_stops: int | None) -> str:
    if max_stops is None:
        return "any number (but prefer fewer stops at similar prices)"
    if max_stops == 0:
        return DIRECT_ONLY_CLAUSE
    return f"at most {max_stops} stop{'s' if max_stops != 1 else ''} each way — skip itineraries with more"


def _describe_window(p: SearchParams) -> str:
    parts = []
    if p.depart_from and p.depart_to:
        parts.append(f"departing between {p.depart_from.isoformat()} and {p.depart_to.isoformat()} (inclusive)")
    elif p.depart_from:
        parts.append(f"departing on or after {p.depart_from.isoformat()}")
    elif p.depart_to:
        parts.append(f"departing on or before {p.depart_to.isoformat()}")
    if p.trip_type == "round_trip" and (p.nights_min is not None or p.nights_max is not None):
        lo, hi = p.nights_min, p.nights_max
        if lo is not None and hi is not None:
            parts.append(f"staying {lo}-{hi} nights" if lo != hi else f"staying {lo} nights")
        elif lo is not None:
            parts.append(f"staying at least {lo} nights")
        else:
            parts.append(f"staying at most {hi} nights")
    if not parts:
        return ""
    return "\n  Hard limits: " + "; ".join(parts) + ". Search specific dates inside this window."


def build_research_prompt(origin: str, destination: str, dates_hint: str,
                          params: SearchParams, today: date,
                          budget: str = f"about {SEARCH_USES} searches") -> str:
    airports_clause = ""
    if params.nearby_airports:
        airports_clause = (
            "\n- Airports: consider ALL airports serving the origin and destination cities plus "
            "alternatives within ~150 km (e.g. London = LHR/LGW/STN/LTN/SEN/LCY), and report "
            "which airport each fare uses"
        )
    budget_clause = ""
    if params.max_price is not None:
        budget_clause = (
            f"\n- Budget: at most {params.max_price:,.0f} {params.currency} in total for the party — "
            "focus on fares under it"
        )
    # Countries ("Greece") can't be told apart from cities here, so the guidance
    # is always present; detection only raises the tool budget (see research()).
    flexible_clause = (
        "\n- If the origin or destination is a list, a country/region or 'anywhere', treat it "
        "like an explore search: compare several concrete airports (for a country, its main "
        "airports; for a region, the cheapest-to-reach cities) and report the best fare for "
        "each of 3-5 different places. Every deal names its concrete city and airport."
    )
    trip_math = ""
    if params.trip_type == "round_trip":
        trip_math = (
            "\n   Month/route pages often show ONE-WAY 'from' fares: for a round trip, add the "
            "return leg (roughly double a one-way fare) unless the source says it is round trip."
        )
    return RESEARCH_PROMPT.format(
        today=today.isoformat(),
        budget=budget,
        trip_math=trip_math,
        travellers=params.passengers.total,
        origin=origin,
        destination=destination,
        trip="one-way" if params.trip_type == "one_way" else "round trip",
        dates_hint=dates_hint,
        date_window=_describe_window(params),
        party=describe_party(params),
        cabin=CABIN_NAMES[params.cabin_class],
        stops=_describe_stops(params.max_stops),
        airports_clause=airports_clause,
        budget_clause=budget_clause,
        flexible_clause=flexible_clause,
        baggage_clause=_baggage_clause(params.cabin_bag, params.checked_bag),
        currency=params.currency,
    )


# --- deterministic post-filters ------------------------------------------------

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CONFIDENCE_RANK = {"verified": 0, "estimated": 1, "from_price": 2}
_ONE_WAY = re.compile(r"\bone[- ]?way\b", re.IGNORECASE)
_ROUND_TRIP = re.compile(r"\b(round[- ]?trip|return (fare|ticket|flight|trip)|both (ways|directions|legs))\b",
                         re.IGNORECASE)
_ALREADY_DOUBLED = re.compile(r"doubl|[x×]\s?2\b|\b2\s?[x×]|return leg added", re.IGNORECASE)


def _one_way_fare_for_round_trip(d: FlightDeal) -> bool:
    """A round-trip search got a price that covers only one direction: the notes
    say one-way, or there is no return date and nothing says the fare is a
    return. Month/route pages mostly show one-way "from" fares, and the model
    reports them as round trips despite the prompt."""
    notes = d.notes or ""
    if _ALREADY_DOUBLED.search(notes):
        return False
    if _ONE_WAY.search(notes):
        return True
    return not d.return_date.strip() and not _ROUND_TRIP.search(notes)


def _as_round_trip(d: FlightDeal) -> FlightDeal:
    """Estimate a round-trip total from a one-way fare: outbound + a return leg at the same price."""
    note = f"Round trip estimated as 2x the one-way fare ({d.price:,.0f} {d.currency})."
    return d.model_copy(update={
        "price": round(d.price * 2, 2),
        "price_per_person": round(d.price_per_person * 2, 2) if d.price_per_person else None,
        "price_confidence": "estimated",
        "notes": f"{d.notes} {note}".strip(),
    })


def parse_iso_date(text: str) -> date | None:
    """First YYYY-MM-DD in `text` ('2026-10-05', '2026-10-05 to 2026-10-09'), else None."""
    m = _ISO_DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _price_in(deal: FlightDeal, currency: str) -> float | None:
    return _convert_currency(deal.price, deal.currency, currency)


def _dedupe_key(d: FlightDeal) -> tuple:
    return (
        (d.origin_airport or d.origin).strip().lower(),
        (d.destination_airport or d.destination).strip().lower(),
        d.airline.strip().lower(),
        d.depart_date.strip().lower(),
        d.return_date.strip().lower(),
        d.flight_numbers.replace(" ", "").lower(),
        d.depart_time.strip(),
    )


def postprocess(deals: list[FlightDeal], params: SearchParams, today: date | None = None) -> list[FlightDeal]:
    """Enforce the search criteria in code and tidy the extracted deals.

    One-way fares reported for a round-trip search are doubled (confidence
    'estimated'), then every deal is normalized (normalize.normalize_deal:
    canonical airline/airport/currency/dates + quality_flags). Drops deals that break max_stops, max_price (after currency conversion),
    the departure window, trip length, trip type, or that depart in the past;
    de-duplicates within the run (keeping the most trustworthy, then cheapest
    sighting) and sorts by price in the preferred currency. Criteria that can't
    be checked (unparseable dates, unknown currency) keep the deal.
    """
    today = today or date.today()
    paying = params.passengers.adults + params.passengers.children
    kept: list[FlightDeal] = []
    for deal in deals:
        d = deal.model_copy(update={
            "origin_airport": deal.origin_airport.strip().upper(),
            "destination_airport": deal.destination_airport.strip().upper(),
            "airline_code": deal.airline_code.strip().upper(),
            "booking_links": [],  # built by booking.build_links, never by the LLM
            "quality_flags": [],  # set by normalize, never by the LLM
        })
        if d.price <= 0:
            continue
        if params.trip_type == "round_trip" and _one_way_fare_for_round_trip(d):
            d = _as_round_trip(d)  # before the budget filter, which must see the real total
        # Canonical names/codes and a sane currency before any filter reads them;
        # after the doubling so the price sanity check sees the round-trip total.
        d = _normalize(d, params, today)
        if d is None:
            continue
        if params.max_stops is not None and d.stops > params.max_stops:
            continue
        if params.max_price is not None:
            converted = _price_in(d, params.currency)
            if converted is not None and converted > params.max_price * 1.005:  # rounding slack
                continue
        out, back = parse_iso_date(d.depart_date), parse_iso_date(d.return_date)
        if out is not None:
            if out < today:
                continue
            if params.depart_from and out < params.depart_from:
                continue
            if params.depart_to and out > params.depart_to:
                continue
        if params.trip_type == "one_way" and back is not None:
            continue  # a round-trip fare is not a one-way price
        if out is not None and back is not None:
            if back < out:
                continue
            nights = (back - out).days
            if params.nights_min is not None and nights < params.nights_min:
                continue
            if params.nights_max is not None and nights > params.nights_max:
                continue
        if d.price_per_person is None or d.price_per_person <= 0:
            d.price_per_person = round(d.price / max(paying, 1), 2)
        kept.append(d)

    # Best sighting per itinerary: verified beats estimated beats teaser, then cheapest.
    kept.sort(key=lambda d: (_CONFIDENCE_RANK.get(d.price_confidence, 1), d.price))
    unique: dict[tuple, FlightDeal] = {}
    for d in kept:
        unique.setdefault(_dedupe_key(d), d)

    def sort_key(d: FlightDeal) -> tuple:
        converted = _price_in(d, params.currency)
        return (converted is None, converted if converted is not None else d.price)

    return sorted(unique.values(), key=sort_key)


# --- graph -----------------------------------------------------------------------

class AgentState(TypedDict):
    origin: str
    destination: str
    dates_hint: str
    params: dict  # SearchParams.model_dump(mode="json")
    research_notes: str
    deals: list[FlightDeal]


def _params_of(state: AgentState) -> SearchParams:
    return SearchParams.model_validate(state.get("params") or {})


def _text_of(message) -> str:
    """Join the text of a model response (content may be str or a block list)."""
    text = getattr(message, "text", None)
    if isinstance(text, str) and text:
        return text
    if callable(text):  # older langchain-core: .text is a method
        return text()
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _blocks_of(content) -> list[dict]:
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _search_query_of(block: dict) -> str:
    """Best-effort extraction of the query (or fetched URL) from a web tool
    block — the exact shape varies by provider and langchain version."""
    for candidate in (block.get("action"), block.get("input"), block):
        if isinstance(candidate, dict):
            for key in ("query", "url"):
                value = candidate.get(key)
                if isinstance(value, str) and value:
                    return value
    return ""


def _stream_research(llm_with_search, messages: list, emit: OnEvent):
    """Stream one research turn, emitting text deltas and web-tool events.
    Returns (text, aggregated message) — the message is needed to continue a paused turn."""
    parts: list[str] = []
    searches_seen = 0
    full = None
    for chunk in llm_with_search.stream(messages):
        full = chunk if full is None else full + chunk
        delta = ""
        text = getattr(chunk, "text", None)
        if isinstance(text, str):
            delta = text
        elif callable(text):
            delta = text()
        elif isinstance(chunk.content, str):
            delta = chunk.content
        if delta:
            parts.append(delta)
            emit({"kind": "delta", "text": delta})
        for block in _blocks_of(chunk.content):
            btype = str(block.get("type", ""))
            name = str(block.get("name", ""))
            if "result" in btype:
                continue
            is_fetch = "fetch" in btype or name == "web_fetch"
            if "search" in btype or "search" in name or is_fetch:
                query = _search_query_of(block)
                if query or not delta:
                    searches_seen += 1
                    label = "open page" if is_fetch else "web search"
                    text_out = (f"open {query}" if is_fetch else query) if query else f"{label} #{searches_seen}"
                    emit({"kind": "search", "text": text_out})
    return "".join(parts), full


# Anthropic pauses a long server-side tool loop (stop_reason "pause_turn");
# resending the conversation lets it pick up where it left off.
MAX_CONTINUATIONS = 2


def _paused(message) -> bool:
    return bool(message is not None
                and (getattr(message, "response_metadata", None) or {}).get("stop_reason") == "pause_turn")


def _as_history(message):
    from langchain_core.messages import AIMessage

    return AIMessage(content=message.content)


def research(state: AgentState, *, on_event: OnEvent | None = None) -> dict:
    from langchain_core.messages import HumanMessage

    params = _params_of(state)
    flexible = params.nearby_airports or is_flexible(state["destination"]) or is_flexible(state["origin"])
    llm, web_tools = _make_llm(flexible)
    llm_with_search = llm.bind_tools(web_tools)
    uses = {t["name"]: t["max_uses"] for t in web_tools if "max_uses" in t}
    budget = (f"about {uses['web_search']} searches and {uses['web_fetch']} page fetches"
              if len(uses) == 2 else f"about {SEARCH_USES} searches")
    prompt = build_research_prompt(state["origin"], state["destination"], state["dates_hint"],
                                   params, date.today(), budget)
    messages: list = [HumanMessage(prompt)]
    notes: list[str] = []
    streaming = on_event is not None
    for turn in range(MAX_CONTINUATIONS + 1):
        message = None
        if streaming:
            try:
                text, message = _stream_research(llm_with_search, messages, on_event)
            except SearchCancelled:
                raise  # a cancel must never trigger the non-streaming fallback
            except Exception:
                logger.exception("streaming research failed, falling back to invoke")
                streaming = False
            else:
                if not text and not _paused(message):
                    logger.warning("streaming returned no text, retrying without streaming")
                    streaming = False
                    message = None
        if message is None:
            message = llm_with_search.invoke(messages)
            text = _text_of(message)
        notes.append(text)
        if not _paused(message):
            break
        logger.info("research paused by the provider (turn %d), continuing", turn + 1)
        if on_event:
            on_event({"kind": "phase", "text": "Still researching…"})
        messages = [*messages, _as_history(message)]
    return {"research_notes": "\n".join(n for n in notes if n)}


def extract(state: AgentState) -> dict:
    params = _params_of(state)
    llm, _ = _make_llm()
    extractor = llm.with_structured_output(FlightDealList)
    prompt = EXTRACT_PROMPT.format(
        origin=state["origin"],
        destination=state["destination"],
        today=date.today().isoformat(),
        trip="one-way" if params.trip_type == "one_way" else "round trip",
        party=describe_party(params),
        cabin=CABIN_NAMES[params.cabin_class],
        notes=state["research_notes"],
    )
    result: FlightDealList = extractor.invoke(prompt)
    return {"deals": postprocess(result.deals, params)}


def build_graph(on_event: OnEvent | None = None):
    def research_node(state: AgentState) -> dict:
        if on_event:
            on_event({"kind": "phase", "text": "Researching the live web for deals…"})
        return research(state, on_event=on_event)

    def extract_node(state: AgentState) -> dict:
        if on_event:
            on_event({"kind": "phase", "text": "Extracting and checking deals against your criteria…"})
        return extract(state)

    graph = StateGraph(AgentState)
    graph.add_node("research", research_node)
    graph.add_node("extract", extract_node)
    graph.add_edge(START, "research")
    graph.add_edge("research", "extract")
    graph.add_edge("extract", END)
    return graph.compile()


def find_deals(
    origin: str,
    destination: str,
    dates_hint: str,
    direct_only: bool = False,
    currency: str = "USD",
    *,
    cabin_bag: bool = False,
    checked_bag: bool = False,
    params: SearchParams | None = None,
    on_event: OnEvent | None = None,
) -> list[FlightDeal]:
    """Run the agent end to end and return the structured, filtered deals.

    `params` carries the full criteria; without it the legacy flags
    (direct_only, currency, bags) build an equivalent SearchParams."""
    if params is None:
        params = SearchParams(
            max_stops=0 if direct_only else None,
            currency=currency,
            cabin_bag=cabin_bag,
            checked_bag=checked_bag,
        )
    app = build_graph(on_event)
    final_state = app.invoke(
        {
            "origin": origin,
            "destination": destination,
            "dates_hint": dates_hint,
            "params": params.model_dump(mode="json"),
            "research_notes": "",
            "deals": [],
        }
    )
    if on_event:
        on_event({"kind": "phase", "text": f"Found {len(final_state['deals'])} deals."})
    return final_state["deals"]
