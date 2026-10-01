"""Per-pass model routing, LLM usage capture (invoke + stream, both providers'
shapes), persistence, cost math and the monthly AI budget. Fake models only —
nothing here reaches a provider."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk

from app import agent, engine, llm_usage, main, storage
from app.models import FlightDeal, FlightDealList, SearchParams

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


# --- fakes -------------------------------------------------------------------------

class FakeLLM:
    """Stands in for ChatOpenAI/ChatAnthropic: canned stream chunks, invoke reply
    and structured-output reply, each carrying provider-shaped usage."""

    def __init__(self, chunks=None, reply=None, structured=None, fail_after=None):
        self.chunks, self.reply, self.structured = chunks or [], reply, structured
        self.fail_after = fail_after
        self.include_raw = None

    def bind_tools(self, tools):
        return self

    def stream(self, messages):
        for i, chunk in enumerate(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise ConnectionError("stream dropped")
            yield chunk

    def invoke(self, messages):
        return self.reply

    def with_structured_output(self, schema, include_raw=False):
        self.include_raw = include_raw
        outer = self

        class Extractor:
            def invoke(self, prompt):
                return outer.structured

        return Extractor()


def _deal(price=120.0) -> FlightDeal:
    return FlightDeal(origin="Tel Aviv", destination="Athens", origin_airport="TLV", destination_airport="ATH",
                      airline="Wizz Air", price=price, currency="USD", depart_date="2026-11-10")


def _extract_reply(usage: dict, response_metadata: dict | None = None) -> dict:
    raw = AIMessage(content="{}", usage_metadata=usage, response_metadata=response_metadata or {})
    return {"raw": raw, "parsed": FlightDealList(deals=[_deal()]), "parsing_error": None}


def _openai_stream() -> list[AIMessageChunk]:
    """Responses API shape: text deltas, web_search_call items, usage on the final chunk."""
    return [
        AIMessageChunk(content=[{"type": "web_search_call", "id": "ws_1", "status": "completed",
                                 "action": {"type": "search", "query": "TLV ATH November"}, "index": 0}]),
        AIMessageChunk(content=[{"type": "text", "text": "DEAL 1 ", "index": 1}]),
        AIMessageChunk(content=[{"type": "web_search_call", "id": "ws_2", "status": "completed",
                                 "action": {"type": "search", "query": "wizz air TLV ATH"}, "index": 2}]),
        AIMessageChunk(content=[{"type": "text", "text": "price: 120 USD", "index": 3}]),
        AIMessageChunk(content=[], usage_metadata={
            "input_tokens": 40_000, "output_tokens": 3_000, "total_tokens": 43_000,
            "input_token_details": {"cache_read": 10_000}, "output_token_details": {"reasoning": 1_000}},
            chunk_position="last"),
    ]


def _anthropic_stream() -> list[AIMessageChunk]:
    """Messages API shape via langchain-anthropic: server_tool_use blocks (merged
    by index), no server_tool_use counter, usage on message_delta."""
    return [
        AIMessageChunk(content=[], response_metadata={"model_name": "claude-opus-5"}),
        AIMessageChunk(content=[{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                                 "input": {}, "index": 0}]),
        AIMessageChunk(content=[{"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                                 "content": [], "index": 1}]),
        AIMessageChunk(content=[{"type": "server_tool_use", "id": "srvtoolu_2", "name": "web_fetch",
                                 "input": {}, "index": 2}]),
        AIMessageChunk(content=[{"type": "text", "text": "DEAL 1 price 120 USD", "index": 3}]),
        AIMessageChunk(content=[], usage_metadata={
            "input_tokens": 20_000, "output_tokens": 2_000, "total_tokens": 22_000,
            "input_token_details": {"cache_read": 5_000, "cache_creation": 1_000}},
            response_metadata={"stop_reason": "end_turn"}),
    ]


def _rows() -> dict[str, dict]:
    return {r["model"]: r for r in storage.llm_usage()}


def _use_fakes(monkeypatch, research: FakeLLM, extract: FakeLLM) -> list[str]:
    purposes: list[str] = []

    def fake_make_llm(flexible=False, purpose="research"):
        purposes.append(purpose)
        return (extract if purpose == "extract" else research), [{"type": "web_search"}]

    monkeypatch.setattr(agent, "_make_llm", fake_make_llm)
    return purposes


# --- model routing ------------------------------------------------------------------

def test_openai_passes_use_separate_models(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("FLIGHT_SCOUT_MODEL", "gpt-5.5")
    assert agent.model_for("research") == "gpt-5.5"
    assert agent.model_for("extract") == "gpt-5.4-mini"
    research, _ = agent._make_llm(purpose="research")
    extract, _ = agent._make_llm(purpose="extract")
    assert (research.model_name, extract.model_name) == ("gpt-5.5", "gpt-5.4-mini")
    assert research.stream_usage and extract.use_responses_api

    monkeypatch.setenv("FLIGHT_SCOUT_EXTRACT_MODEL", "gpt-5.5")
    assert agent._make_llm(purpose="extract")[0].model_name == "gpt-5.5"


def test_anthropic_extract_defaults_to_the_cheap_tier(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert agent.model_for("research") == "claude-opus-5"
    assert agent.model_for("extract") == "claude-haiku-4-5"
    assert agent._make_llm(purpose="extract")[0].model == "claude-haiku-4-5"


def test_extract_pass_asks_for_the_extract_model_and_raw_usage(temp_db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    research = FakeLLM(reply=AIMessage(content="DEAL 1 price 120 USD"))
    extract = FakeLLM(structured=_extract_reply({"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}))
    purposes = _use_fakes(monkeypatch, research, extract)
    deals = agent.find_deals("TLV", "ATH", "November", params=SearchParams(trip_type="one_way"))
    assert purposes == ["research", "extract"]
    assert extract.include_raw is True
    assert [d.price for d in deals] == [120.0]


def test_extract_parsing_error_still_records_usage(temp_db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reply = _extract_reply({"input_tokens": 500, "output_tokens": 50, "total_tokens": 550})
    reply.update(parsed=None, parsing_error=ValueError("bad json"))
    _use_fakes(monkeypatch, FakeLLM(reply=AIMessage(content="notes")), FakeLLM(structured=reply))
    with pytest.raises(ValueError, match="bad json"):
        agent.find_deals("TLV", "ATH", "November", params=SearchParams(trip_type="one_way"))
    assert _rows()["gpt-5.4-mini"]["input_tokens"] == 500


# --- usage capture ------------------------------------------------------------------

def test_usage_of_openai_invoke_counts_web_search_calls():
    message = AIMessage(
        content=[{"type": "web_search_call", "id": "ws_1", "action": {"type": "search"}},
                 {"type": "web_search_call", "id": "ws_2", "action": {"type": "search"}},
                 {"type": "text", "text": "notes"}],
        usage_metadata={"input_tokens": 12_000, "output_tokens": 3_000, "total_tokens": 15_000,
                        "input_token_details": {"cache_read": 2_000}})
    assert llm_usage.usage_of(message, "openai") == {
        "calls": 1, "input_tokens": 12_000, "cached_input_tokens": 2_000, "cache_write_tokens": 0,
        "output_tokens": 3_000, "web_searches": 2, "web_fetches": 0}


def test_usage_of_anthropic_invoke_prefers_the_server_tool_counter():
    message = AIMessage(
        content=[{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}}],
        usage_metadata={"input_tokens": 9_000, "output_tokens": 800, "total_tokens": 9_800,
                        "input_token_details": {"cache_read": 3_000, "cache_creation": 0,
                                                "ephemeral_5m_input_tokens": 1_000}},
        response_metadata={"usage": {"input_tokens": 5_000, "output_tokens": 800,
                                     "cache_read_input_tokens": 3_000, "cache_creation_input_tokens": 1_000,
                                     "server_tool_use": {"web_search_requests": 4, "web_fetch_requests": 2}}})
    counts = llm_usage.usage_of(message, "anthropic")
    assert counts["web_searches"] == 4 and counts["web_fetches"] == 2
    assert (counts["input_tokens"], counts["cached_input_tokens"], counts["cache_write_tokens"]) == (9_000, 3_000, 1_000)


def test_usage_of_falls_back_to_raw_response_usage():
    anthropic = AIMessage(content="x", response_metadata={"usage": {
        "input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0}})
    assert llm_usage.usage_of(anthropic, "anthropic")["input_tokens"] == 150  # Anthropic excludes cache reads
    openai = AIMessage(content="x", response_metadata={"token_usage": {
        "input_tokens": 100, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 40}}})
    counts = llm_usage.usage_of(openai, "openai")
    assert (counts["input_tokens"], counts["cached_input_tokens"], counts["output_tokens"]) == (100, 40, 10)


def test_streamed_openai_search_records_both_passes(temp_db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("FLIGHT_SCOUT_MODEL", "gpt-5.5")
    extract = FakeLLM(structured=_extract_reply(
        {"input_tokens": 4_000, "output_tokens": 600, "total_tokens": 4_600}))
    _use_fakes(monkeypatch, FakeLLM(chunks=_openai_stream()), extract)
    events: list[dict] = []
    agent.find_deals("TLV", "ATH", "November", params=SearchParams(trip_type="one_way"), on_event=events.append)

    rows = _rows()
    research = rows["gpt-5.5"]
    assert (research["calls"], research["input_tokens"], research["cached_input_tokens"],
            research["output_tokens"], research["web_searches"]) == (1, 40_000, 10_000, 3_000, 2)
    assert (rows["gpt-5.4-mini"]["input_tokens"], rows["gpt-5.4-mini"]["web_searches"]) == (4_000, 0)
    assert storage.provider_calls(llm_usage.SEARCH_COUNTER) == 1
    assert any(e["kind"] == "delta" for e in events)


def test_streamed_anthropic_search_counts_server_tool_blocks(temp_db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    extract = FakeLLM(structured=_extract_reply(
        {"input_tokens": 3_000, "output_tokens": 400, "total_tokens": 3_400},
        {"usage": {"input_tokens": 3_000, "output_tokens": 400}}))
    _use_fakes(monkeypatch, FakeLLM(chunks=_anthropic_stream()), extract)
    agent.find_deals("TLV", "ATH", "November", params=SearchParams(trip_type="one_way"), on_event=lambda e: None)

    rows = _rows()
    opus = rows["claude-opus-5"]
    assert (opus["input_tokens"], opus["cached_input_tokens"], opus["cache_write_tokens"],
            opus["output_tokens"], opus["web_searches"], opus["web_fetches"]) == (20_000, 5_000, 1_000, 2_000, 1, 1)
    assert rows["claude-haiku-4-5"]["output_tokens"] == 400


def test_a_dropped_stream_still_counts_its_searches_and_the_invoke_retry(temp_db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    retry = AIMessage(content="DEAL 1 price 120 USD",
                      usage_metadata={"input_tokens": 30_000, "output_tokens": 2_000, "total_tokens": 32_000})
    research = FakeLLM(chunks=_openai_stream(), reply=retry, fail_after=3)
    extract = FakeLLM(structured=_extract_reply({"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}))
    _use_fakes(monkeypatch, research, extract)
    agent.find_deals("TLV", "ATH", "November", params=SearchParams(trip_type="one_way"), on_event=lambda e: None)
    row = _rows()["gpt-5.4-mini"]  # research + extract share the default model here
    assert row["calls"] == 3  # partial stream + invoke fallback + extract
    assert row["web_searches"] == 2  # seen before the stream dropped
    assert row["input_tokens"] == 30_001


def test_recording_never_breaks_a_search(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage, "record_llm_usage", broken)
    assert llm_usage.record("openai", "gpt-5.5", AIMessage(content="x")) is None


def test_usage_accumulates_per_month_and_model(temp_db):
    counts = {"calls": 1, "input_tokens": 100, "output_tokens": 10, "web_searches": 1}
    storage.record_llm_usage("openai", "gpt-5.5", counts, NOW)
    storage.record_llm_usage("openai", "gpt-5.5", counts, NOW)
    storage.record_llm_usage("openai", "gpt-5.4-mini", counts, NOW)
    storage.record_llm_usage("openai", "gpt-5.5", counts, datetime(2026, 11, 1, tzinfo=timezone.utc))
    rows = {r["model"]: r for r in storage.llm_usage(NOW)}
    assert (rows["gpt-5.5"]["calls"], rows["gpt-5.5"]["input_tokens"], rows["gpt-5.5"]["web_searches"]) == (2, 200, 2)
    assert rows["gpt-5.4-mini"]["calls"] == 1
    assert len(storage.llm_usage(datetime(2026, 11, 2, tzinfo=timezone.utc))) == 1


# --- cost math ----------------------------------------------------------------------

def test_cost_of_openai_row():
    row = {"model": "gpt-5.5", "input_tokens": 1_000_000, "cached_input_tokens": 200_000,
           "output_tokens": 100_000, "web_searches": 10}
    # 800k x $5 + 200k x $0.50 + 100k x $30 per 1M, + 10 searches x $10/1k
    assert llm_usage.cost_of(row, "openai") == pytest.approx(4.0 + 0.1 + 3.0 + 0.1)


def test_cost_of_anthropic_row_prices_cache_writes():
    row = {"model": "claude-haiku-4-5", "input_tokens": 1_000_000, "cached_input_tokens": 400_000,
           "cache_write_tokens": 100_000, "output_tokens": 200_000, "web_searches": 0}
    assert llm_usage.cost_of(row, "anthropic") == pytest.approx(0.5 * 1 + 0.4 * 0.1 + 0.1 * 1.25 + 0.2 * 5)


def test_dated_snapshots_unknown_models_and_env_overrides(monkeypatch):
    assert llm_usage.price_for("gpt-5.4-mini-2026-03-17")["input"] == 0.75
    assert llm_usage.price_for("gpt-5.4") is None  # not a prefix match of gpt-5.4-mini
    assert llm_usage.cost_of({"model": "mystery-1", "input_tokens": 10}, "openai") is None

    monkeypatch.setenv("FLIGHT_SCOUT_PRICES_JSON",
                       '{"models": {"mystery-1": {"input": 2, "output": 4}}, "web_search_per_1k": {"openai": 25}}')
    assert llm_usage.cost_of({"model": "mystery-1", "input_tokens": 1_000_000, "output_tokens": 0,
                              "web_searches": 2}, "openai") == pytest.approx(2.05)
    monkeypatch.setenv("FLIGHT_SCOUT_PRICES_JSON", '{"gpt-5.5": {"input": 1}}')  # bare models map
    assert llm_usage.price_for("gpt-5.5") == {"input": 1, "cached_input": 0.50, "output": 30.00}
    monkeypatch.setenv("FLIGHT_SCOUT_PRICES_JSON", "not json")
    assert llm_usage.price_for("gpt-5.5")["input"] == 5.00


def test_usage_endpoint_reports_ai_spend(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("FLIGHT_SCOUT_MODEL", "gpt-5.5")
    storage.record_llm_usage("openai", "gpt-5.5", {"calls": 2, "input_tokens": 200_000, "output_tokens": 20_000,
                                                   "web_searches": 6})
    storage.record_llm_usage("openai", "mystery-1", {"calls": 1, "input_tokens": 1_000})
    llm_usage.record_search()
    ai = TestClient(main.app).get("/api/usage").json()["ai"]
    assert ai["configured"] and ai["searches"] == 1 and ai["web_searches"] == 6
    assert ai["cost_usd"] == pytest.approx(200_000 * 5 / 1e6 + 20_000 * 30 / 1e6 + 0.06)
    assert (ai["research_model"], ai["extract_model"]) == ("gpt-5.5", "gpt-5.4-mini")
    by_model = {m["model"]: m for m in ai["models"]}
    assert by_model["mystery-1"]["cost_usd"] is None and by_model["mystery-1"]["estimated"]
    assert ai["estimated"] and ai["prices_as_of"] == llm_usage.PRICES_AS_OF
    assert ai["budget_usd"] is None and ai["warning"] == ""


# --- budget -------------------------------------------------------------------------

def _spend(dollars: float) -> None:
    """Record gpt-5.4-mini output tokens worth `dollars` ($4.50 per 1M)."""
    storage.record_llm_usage("openai", "gpt-5.4-mini",
                             {"calls": 1, "output_tokens": round(dollars / 4.50 * 1_000_000)})


def test_over_budget_refuses_llm_searches_before_any_model_call(temp_db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("FLIGHT_SCOUT_AI_BUDGET_USD", "2")
    _spend(2.10)

    def no_model(*a, **k):
        raise AssertionError("no model call once the budget is spent")

    monkeypatch.setattr(agent, "_make_llm", no_model)
    with pytest.raises(llm_usage.AIBudgetExceeded, match="budget"):
        agent.find_deals("TLV", "ATH", "November")

    # Through the engine (no fare-API keys -> the agent path): no deals, a clear message.
    outcome = engine.search("TLV", "Europe", "November", SearchParams())
    assert outcome.deals == []
    assert any("AI budget" in m and "Fare-API searches still work" in m for m in outcome.meta.messages)
    assert storage.provider_calls(llm_usage.SEARCH_COUNTER) == 0

    report = TestClient(main.app).get("/api/usage").json()["ai"]
    assert report["over_budget"] and report["budget_usd"] == 2


def test_warns_at_80_percent_but_still_searches(temp_db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("FLIGHT_SCOUT_AI_BUDGET_USD", "10")
    _spend(8.5)
    called = []

    def fake_find_deals(origin, destination, dates_hint, *args, params=None, on_event=None, leads="", **kw):
        called.append(destination)
        return [_deal()]

    monkeypatch.setattr(agent, "find_deals", fake_find_deals)
    outcome = engine.search("TLV", "Europe", "November", SearchParams(trip_type="one_way"))
    assert called == ["Europe"] and len(outcome.deals) == 1
    assert any("of the $10.00 budget (85%)" in m for m in outcome.meta.messages)
    assert "85%" in llm_usage.spend(True).warning


def test_no_budget_means_no_db_reads(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("budget check without a budget must not query usage")

    monkeypatch.setattr(storage, "llm_usage", boom)
    assert llm_usage.check_budget() == ("ok", "")
    monkeypatch.setenv("FLIGHT_SCOUT_AI_BUDGET_USD", "lots")
    assert llm_usage.budget_usd() is None
