"""LangGraph agent: search the live web for cheap flights, then extract structured deals.

Graph shape:

    START -> research -> extract -> END

- ``research`` calls the model with its provider's server-side web-search tool bound,
  so the searching/fetching loop runs on the provider's side and the node gets back
  prose findings.
- ``extract`` runs a second structured-output pass that turns those findings into a
  validated ``FlightDealList``.

Works with either Anthropic (ANTHROPIC_API_KEY) or OpenAI (OPENAI_API_KEY); the
provider is auto-detected from whichever key is set.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from .models import FlightDeal, FlightDealList

load_dotenv()

logger = logging.getLogger(__name__)

# Progress callback: receives {"kind": "phase"|"delta"|"search", "text": str}.
# "delta" is a streamed chunk of the research pass's prose; "search" fires when
# the model issues a web-search call. Used by the API to live-stream agent
# activity to the UI; None (the watcher/CLI case) disables it entirely.
OnEvent = Callable[[dict], None]

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    # mini-tier is plenty for search-and-extract work and far cheaper;
    # override with FLIGHT_SCOUT_MODEL if you want a bigger model.
    "openai": "gpt-5.4-mini",
}

# Server-side web search: executed on the provider's infrastructure, results come
# back inside the same model response — no client tool loop needed.
WEB_SEARCH_TOOLS = {
    "anthropic": {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
    "openai": {"type": "web_search"},
}


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


def _make_llm():
    """Return (chat model, web-search tool dict) for the active provider."""
    provider = _provider()
    model = os.environ.get("FLIGHT_SCOUT_MODEL", DEFAULT_MODELS[provider])
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, max_tokens=16000), WEB_SEARCH_TOOLS["anthropic"]
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        # Built-in web search requires OpenAI's Responses API.
        return ChatOpenAI(model=model, use_responses_api=True), WEB_SEARCH_TOOLS["openai"]
    raise RuntimeError(f"Unknown FLIGHT_SCOUT_PROVIDER: {provider}")

RESEARCH_PROMPT = """You are a flight-deal researcher.

Find the cheapest flights from {origin} to {destination}, dates: {dates_hint}.{direct_clause}

Search the web (Google Flights results pages, airline deal pages, deal aggregators like
Secret Flying / Fly4Free, and travel fare posts). Prefer recent results — prices go stale fast.

For every concrete deal you find, report:
- total price and currency
- airline(s)
- departure date (and return date if round trip)
- flight duration and number of stops when available
- the URL where the fare can be booked or tracked, and the URL of the page you found it on
- caveats (hand-luggage-only fare, deal expiry, mistake fare, etc.)

Report at least 3 distinct options when possible, cheapest first. Quote prices in {currency}
when the source offers it; otherwise give the original price with its currency clearly named.
If prices are only available as ranges or "from X" figures, report them with that caveat.
Finish with a short plain-text list of the deals — no tables needed."""

EXTRACT_PROMPT = """Below are research notes about flight deals from {origin} to {destination}.
Extract every distinct deal into the structured format. Use numeric prices, and put the
currency in the currency field as an ISO 4217 code (USD, EUR, ILS, GBP...) — never a symbol.
If a field is unknown, use an empty string (or 0 for stops).
Skip anything that is not an actual priced flight deal.

Research notes:
{notes}"""


DIRECT_ONLY_CLAUSE = (
    "\nIMPORTANT: only DIRECT (non-stop) flights qualify — skip every itinerary "
    "with a connection or stopover, even if it is cheaper."
)


class AgentState(TypedDict):
    origin: str
    destination: str
    dates_hint: str
    direct_only: bool
    currency: str
    research_notes: str
    deals: list[FlightDeal]


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
    """Best-effort extraction of the query from a web-search tool block —
    the exact shape varies by provider and langchain version."""
    for candidate in (block.get("action"), block.get("input"), block):
        if isinstance(candidate, dict):
            query = candidate.get("query")
            if isinstance(query, str) and query:
                return query
    return ""


def _stream_research(llm_with_search, prompt: str, emit: OnEvent) -> str:
    """Stream the research pass, emitting text deltas and web-search events."""
    parts: list[str] = []
    searches_seen = 0
    for chunk in llm_with_search.stream(prompt):
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
            if "search" in btype and "result" not in btype:
                query = _search_query_of(block)
                if query or not delta:
                    searches_seen += 1
                    emit({"kind": "search", "text": query or f"web search #{searches_seen}"})
    return "".join(parts)


def research(state: AgentState, *, on_event: OnEvent | None = None) -> dict:
    llm, web_search_tool = _make_llm()
    llm_with_search = llm.bind_tools([web_search_tool])
    prompt = RESEARCH_PROMPT.format(
        origin=state["origin"],
        destination=state["destination"],
        dates_hint=state["dates_hint"],
        direct_clause=DIRECT_ONLY_CLAUSE if state["direct_only"] else "",
        currency=state["currency"],
    )
    if on_event is not None:
        try:
            notes = _stream_research(llm_with_search, prompt, on_event)
            if notes:
                return {"research_notes": notes}
            logger.warning("streaming returned no text, retrying without streaming")
        except Exception:
            logger.exception("streaming research failed, falling back to invoke")
    response = llm_with_search.invoke(prompt)
    return {"research_notes": _text_of(response)}


def extract(state: AgentState) -> dict:
    llm, _ = _make_llm()
    extractor = llm.with_structured_output(FlightDealList)
    prompt = EXTRACT_PROMPT.format(
        origin=state["origin"],
        destination=state["destination"],
        notes=state["research_notes"],
    )
    result: FlightDealList = extractor.invoke(prompt)
    deals = sorted(result.deals, key=lambda d: d.price)
    # Hard guarantee on top of the prompt: the model occasionally reports a
    # 1-stop fare anyway, so filter rather than trust.
    if state["direct_only"]:
        deals = [d for d in deals if d.stops == 0]
    return {"deals": deals}


def build_graph(on_event: OnEvent | None = None):
    def research_node(state: AgentState) -> dict:
        if on_event:
            on_event({"kind": "phase", "text": "Researching the live web for deals…"})
        return research(state, on_event=on_event)

    def extract_node(state: AgentState) -> dict:
        if on_event:
            on_event({"kind": "phase", "text": "Extracting structured deals from the findings…"})
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
    on_event: OnEvent | None = None,
) -> list[FlightDeal]:
    """Run the agent end to end and return the structured deals."""
    app = build_graph(on_event)
    final_state = app.invoke(
        {
            "origin": origin,
            "destination": destination,
            "dates_hint": dates_hint,
            "direct_only": direct_only,
            "currency": currency,
            "research_notes": "",
            "deals": [],
        }
    )
    if on_event:
        on_event({"kind": "phase", "text": f"Found {len(final_state['deals'])} deals."})
    return final_state["deals"]
