"""LangGraph agent: search the live web for cheap flights, then extract structured deals.

Graph shape:

    START -> research -> extract -> END

- ``research`` calls Claude with Anthropic's server-side web-search tool bound, so the
  searching/fetching loop runs on Anthropic's side and the node gets back prose findings.
- ``extract`` runs a second, cheaper structured-output pass that turns those findings
  into a validated ``FlightDealList``.
"""

from __future__ import annotations

import os
from typing import TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, START, StateGraph

from .models import FlightDeal, FlightDealList

load_dotenv()

MODEL = os.environ.get("FLIGHT_SCOUT_MODEL", "claude-opus-5")

# Anthropic server-side web search: executed on Anthropic's infrastructure,
# results come back inside the same model response — no client tool loop needed.
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 8,
}

RESEARCH_PROMPT = """You are a flight-deal researcher.

Find the cheapest flights from {origin} to {destination}, dates: {dates_hint}.

Search the web (Google Flights results pages, airline deal pages, deal aggregators like
Secret Flying / Fly4Free, and travel fare posts). Prefer recent results — prices go stale fast.

For every concrete deal you find, report:
- total price and currency
- airline(s)
- departure date (and return date if round trip)
- flight duration and number of stops when available
- the URL where the fare can be booked or tracked, and the URL of the page you found it on
- caveats (hand-luggage-only fare, deal expiry, mistake fare, etc.)

Report at least 3 distinct options when possible, cheapest first. If prices are only
available as ranges or "from X" figures, report them with that caveat. Finish with a short
plain-text list of the deals — no tables needed."""

EXTRACT_PROMPT = """Below are research notes about flight deals from {origin} to {destination}.
Extract every distinct deal into the structured format. Use numeric prices (strip currency
symbols into the currency field). If a field is unknown, use an empty string (or 0 for stops).
Skip anything that is not an actual priced flight deal.

Research notes:
{notes}"""


class AgentState(TypedDict):
    origin: str
    destination: str
    dates_hint: str
    research_notes: str
    deals: list[FlightDeal]


def _text_of(message) -> str:
    """Join the text blocks of a model response (content may be str or a block list)."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def research(state: AgentState) -> dict:
    llm = ChatAnthropic(model=MODEL, max_tokens=16000)
    llm_with_search = llm.bind_tools([WEB_SEARCH_TOOL])
    prompt = RESEARCH_PROMPT.format(
        origin=state["origin"],
        destination=state["destination"],
        dates_hint=state["dates_hint"],
    )
    response = llm_with_search.invoke(prompt)
    return {"research_notes": _text_of(response)}


def extract(state: AgentState) -> dict:
    llm = ChatAnthropic(model=MODEL, max_tokens=16000)
    extractor = llm.with_structured_output(FlightDealList)
    prompt = EXTRACT_PROMPT.format(
        origin=state["origin"],
        destination=state["destination"],
        notes=state["research_notes"],
    )
    result: FlightDealList = extractor.invoke(prompt)
    deals = sorted(result.deals, key=lambda d: d.price)
    return {"deals": deals}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("research", research)
    graph.add_node("extract", extract)
    graph.add_edge(START, "research")
    graph.add_edge("research", "extract")
    graph.add_edge("extract", END)
    return graph.compile()


def find_deals(origin: str, destination: str, dates_hint: str) -> list[FlightDeal]:
    """Run the agent end to end and return the structured deals."""
    app = build_graph()
    final_state = app.invoke(
        {
            "origin": origin,
            "destination": destination,
            "dates_hint": dates_hint,
            "research_notes": "",
            "deals": [],
        }
    )
    return final_state["deals"]
