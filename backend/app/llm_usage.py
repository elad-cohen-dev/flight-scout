"""LLM token accounting, the AI spend estimate and the optional monthly AI budget.

Every model call in app/agent.py hands its response to `record()`, which reads
the token counts LangChain attaches (``AIMessage.usage_metadata``, falling back
to the raw ``response_metadata["usage"]``) plus the paid web-search calls, and
adds them to this month's row for that model (storage.llm_usage). `spend()`
prices those rows with the table below for GET /api/usage.

The figure is an ESTIMATE: list prices x the token counts the provider
reported. It ignores negotiated discounts, taxes, and calls whose usage never
came back (e.g. a stream that died mid-way). Check the provider's billing page
for the real number.

Web searches are billed per call on top of tokens:
- OpenAI Responses API: one ``web_search_call`` output item per call (the
  response carries no per-call usage counter, so we count those items).
- Anthropic: ``usage.server_tool_use.web_search_requests`` when the response
  carries it (invoke), else the ``server_tool_use`` blocks named web_search
  (streaming, where LangChain drops that counter). Web fetch is free beyond tokens.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from . import storage
from .models import AIModelSpend, AISpend

logger = logging.getLogger(__name__)

# --- prices -------------------------------------------------------------------
#
# Prices as of 2026-10-01 — UPDATE HERE when the providers change them (or set
# FLIGHT_SCOUT_PRICES_JSON, see _prices()). USD per 1M tokens; web search per
# 1,000 calls. Sources: developers.openai.com/api/docs/pricing and
# platform.claude.com/docs/en/about-claude/pricing, both read on 2026-10-01.
# "estimated": True marks a price we could not verify; the UI shows it as such.
PRICES_AS_OF = "2026-10-01"

MODEL_PRICES: dict[str, dict] = {
    # OpenAI, standard tier. Cache writes aren't billed separately.
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    # Anthropic. cache_write = 5-minute cache writes (1.25x input).
    "claude-opus-5": {"input": 5.00, "cached_input": 0.50, "cache_write": 6.25, "output": 25.00},
    "claude-opus-5-5": {"input": 4.00, "cached_input": 0.20, "cache_write": 5.00, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "cached_input": 0.10, "cache_write": 1.25, "output": 5.00},
}

# OpenAI: $10/1k calls for reasoning models (gpt-5.x), search content billed as
# input tokens (already in the token counts). Anthropic: $10/1k searches.
WEB_SEARCH_PER_1K: dict[str, float] = {"openai": 10.00, "anthropic": 10.00}

WARN_AT = 0.8  # share of FLIGHT_SCOUT_AI_BUDGET_USD that triggers the warning


def _prices() -> tuple[dict[str, dict], dict[str, float]]:
    """The price table, with FLIGHT_SCOUT_PRICES_JSON merged over it. Either
    ``{"models": {"gpt-5.5": {"input": 5, "output": 30, ...}}, "web_search_per_1k": {"openai": 10}}``
    or just the models map. A malformed value is logged and ignored."""
    models = {k: dict(v) for k, v in MODEL_PRICES.items()}
    search = dict(WEB_SEARCH_PER_1K)
    raw = os.environ.get("FLIGHT_SCOUT_PRICES_JSON", "").strip()
    if not raw:
        return models, search
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        model_part = data.get("models", {} if "web_search_per_1k" in data else data)
        for name, entry in model_part.items():
            models[name] = {**models.get(name, {}), **entry}
        for provider, price in (data.get("web_search_per_1k") or {}).items():
            search[provider] = float(price)
    except Exception as exc:  # noqa: BLE001 - a typo in an env var must not break searches
        logger.warning("ignoring FLIGHT_SCOUT_PRICES_JSON: %s", exc)
        return {k: dict(v) for k, v in MODEL_PRICES.items()}, dict(WEB_SEARCH_PER_1K)
    return models, search


def price_for(model: str, models: dict[str, dict] | None = None) -> dict | None:
    """Prices for `model`: an exact entry, else the longest entry it starts with
    (dated snapshots such as 'gpt-5.5-2026-08-01'), else None."""
    models = models if models is not None else _prices()[0]
    if model in models:
        return models[model]
    matches = [k for k in models if model.startswith(k + "-")]
    return models[max(matches, key=len)] if matches else None


def cost_of(row: dict, provider: str, models: dict[str, dict] | None = None,
            search_prices: dict[str, float] | None = None) -> float | None:
    """Estimated USD for one usage row (storage.llm_usage); None when the model
    has no price. input_tokens includes cached reads and cache writes."""
    if models is None or search_prices is None:
        models, search_prices = _prices()
    price = price_for(row["model"], models)
    searches = (row.get("web_searches") or 0) * search_prices.get(provider, 0.0) / 1000
    if price is None:
        return None
    cached = row.get("cached_input_tokens") or 0
    written = row.get("cache_write_tokens") or 0
    fresh = max(0, (row.get("input_tokens") or 0) - cached - written)
    per_token = (
        fresh * price.get("input", 0)
        + cached * price.get("cached_input", price.get("input", 0))
        + written * price.get("cache_write", price.get("input", 0))
        + (row.get("output_tokens") or 0) * price.get("output", 0)
    )
    return per_token / 1_000_000 + searches


# --- capture ------------------------------------------------------------------

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _blocks(message) -> list[dict]:
    content = getattr(message, "content", None)
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _count_blocks(message, provider: str) -> tuple[int, int]:
    """(web searches, page fetches) from the response's content blocks."""
    searches: set = set()
    fetches: set = set()
    for i, block in enumerate(_blocks(message)):
        key = block.get("id") or i
        btype = str(block.get("type", ""))
        if provider == "openai" and btype == "web_search_call":
            searches.add(key)
        elif btype == "server_tool_use":
            if block.get("name") == "web_search":
                searches.add(key)
            elif block.get("name") == "web_fetch":
                fetches.add(key)
    return len(searches), len(fetches)


def usage_of(message, provider: str) -> dict:
    """Token and tool counts of one model response (storage._LLM_COUNTERS keys)."""
    um = getattr(message, "usage_metadata", None) or {}
    rm = getattr(message, "response_metadata", None) or {}
    raw = rm.get("usage") or rm.get("token_usage") or {}
    if um:
        details = um.get("input_token_details") or {}
        input_tokens = um.get("input_tokens") or 0
        output_tokens = um.get("output_tokens") or 0
        cached = details.get("cache_read") or 0
        written = (details.get("cache_creation") or 0) + (details.get("ephemeral_5m_input_tokens") or 0) \
            + (details.get("ephemeral_1h_input_tokens") or 0)
    elif provider == "anthropic":
        cached = _get(raw, "cache_read_input_tokens") or 0
        written = _get(raw, "cache_creation_input_tokens") or 0
        input_tokens = (_get(raw, "input_tokens") or 0) + cached + written  # Anthropic excludes cache
        output_tokens = _get(raw, "output_tokens") or 0
    else:
        input_tokens = _get(raw, "input_tokens", _get(raw, "prompt_tokens")) or 0
        output_tokens = _get(raw, "output_tokens", _get(raw, "completion_tokens")) or 0
        details = _get(raw, "input_tokens_details", _get(raw, "prompt_tokens_details")) or {}
        cached = _get(details, "cached_tokens") or 0
        written = 0
    searches, fetches = _count_blocks(message, provider)
    server = _get(raw, "server_tool_use") or {}
    if _get(server, "web_search_requests") is not None:
        searches = int(_get(server, "web_search_requests"))
    if _get(server, "web_fetch_requests") is not None:
        fetches = int(_get(server, "web_fetch_requests"))
    return {
        "calls": 1,
        "input_tokens": int(input_tokens),
        "cached_input_tokens": int(cached),
        "cache_write_tokens": int(written),
        "output_tokens": int(output_tokens),
        "web_searches": searches,
        "web_fetches": fetches,
    }


def record(provider: str, model: str, message, now: datetime | None = None) -> dict | None:
    """Persist one response's usage. Never raises: accounting must not fail a search."""
    if message is None:
        return None
    try:
        counts = usage_of(message, provider)
        storage.record_llm_usage(provider, model, counts, now)
        return counts
    except Exception:  # noqa: BLE001
        logger.exception("could not record LLM usage for %s", model)
        return None


SEARCH_COUNTER = "llm_search"  # storage.provider_usage row: LLM agent runs this month


def record_search(now: datetime | None = None) -> None:
    try:
        storage.record_provider_call(SEARCH_COUNTER, now)
    except Exception:  # noqa: BLE001
        logger.exception("could not count the LLM search")


# --- spend & budget -------------------------------------------------------------

def budget_usd() -> float | None:
    raw = os.environ.get("FLIGHT_SCOUT_AI_BUDGET_USD", "").strip()
    try:
        value = float(raw) if raw else None
    except ValueError:
        logger.warning("ignoring FLIGHT_SCOUT_AI_BUDGET_USD=%r (not a number)", raw)
        return None
    return value if value is not None and value > 0 else None


def spent_usd(now: datetime | None = None) -> float:
    models, search_prices = _prices()
    total = 0.0
    for row in storage.llm_usage(now):
        cost = cost_of(row, row["provider"], models, search_prices)
        total += cost or 0.0
    return total


def budget_state(now: datetime | None = None) -> tuple[str, float, float | None]:
    """('ok' | 'warn' | 'over', spent this month, budget). Without a budget: always 'ok'."""
    budget = budget_usd()
    if budget is None:
        return "ok", 0.0, None
    spent = spent_usd(now)
    if spent >= budget:
        return "over", spent, budget
    return ("warn" if spent >= budget * WARN_AT else "ok"), spent, budget


def budget_message(state: str, spent: float, budget: float | None) -> str:
    if budget is None or state == "ok":
        return ""
    if state == "over":
        return (f"AI budget for this month used up (~${spent:,.2f} of ${budget:,.2f}, "
                "FLIGHT_SCOUT_AI_BUDGET_USD): web (LLM) searches are paused until next month. "
                "Fare-API searches still work.")
    return f"AI spend this month is ~${spent:,.2f} of the ${budget:,.2f} budget ({spent / budget:.0%})."


class AIBudgetExceeded(RuntimeError):
    """Raised by agent.find_deals before any model call once the monthly AI budget is spent."""


def check_budget(now: datetime | None = None) -> tuple[str, str]:
    """Raise AIBudgetExceeded when over budget; else (state, warning text)."""
    state, spent, budget = budget_state(now)
    message = budget_message(state, spent, budget)
    if state == "over":
        raise AIBudgetExceeded(message)
    return state, message


def spend(configured: bool, research_model: str = "", extract_model: str = "",
          now: datetime | None = None) -> AISpend:
    """This month's estimated AI spend, for GET /api/usage."""
    now = now or datetime.now(timezone.utc)
    models, search_prices = _prices()
    rows: list[AIModelSpend] = []
    total = 0.0
    estimated = False
    for row in storage.llm_usage(now):
        price = price_for(row["model"], models)
        cost = cost_of(row, row["provider"], models, search_prices)
        unverified = price is None or bool(price.get("estimated"))
        estimated = estimated or unverified
        total += cost or 0.0
        rows.append(AIModelSpend(
            provider=row["provider"], model=row["model"], calls=row["calls"],
            input_tokens=row["input_tokens"], cached_input_tokens=row["cached_input_tokens"],
            output_tokens=row["output_tokens"], web_searches=row["web_searches"],
            web_fetches=row["web_fetches"],
            cost_usd=round(cost, 4) if cost is not None else None, estimated=unverified,
        ))
    budget = budget_usd()
    state = "ok"
    if budget is not None:
        state = "over" if total >= budget else ("warn" if total >= budget * WARN_AT else "ok")
    return AISpend(
        configured=configured,
        month=now.strftime("%Y-%m"),
        cost_usd=round(total, 4),
        searches=storage.provider_calls(SEARCH_COUNTER, now),
        web_searches=sum(r.web_searches for r in rows),
        models=rows,
        estimated=estimated,
        prices_as_of=PRICES_AS_OF,
        budget_usd=budget,
        over_budget=state == "over",
        warning=budget_message(state, total, budget),
        research_model=research_model,
        extract_model=extract_model,
    )
