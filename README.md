# ✈️ Flight Scout

An AI agent that hunts the live web for cheap flights, stores what it finds, and shows the results in a web app.

## How it works

```
React app ──POST /api/search──▶ FastAPI ──▶ LangGraph agent ──▶ SQLite
   ▲                                            │
   └────────GET /api/deals──────────────────────┘

LangGraph agent (backend/app/agent.py):
  START ─▶ research ─▶ extract ─▶ END
            │             │
            │             └─ Claude structured output → validated FlightDeal list
            └─ Claude + Anthropic server-side web search (searches Google Flights
               results, airline deal pages, fare aggregators — live, on every run)
```

- **Agent**: [LangGraph](https://langchain-ai.github.io/langgraph/) (Python) driving an LLM with the provider's **server-side web search tool** — no scraping infrastructure or flight-API keys needed. Works with **Anthropic** (Claude) or **OpenAI** (GPT); the provider is auto-detected from whichever API key you set.
- **Storage**: SQLite (`backend/flight_scout.db`), created automatically.
- **API**: FastAPI — `POST /api/search` runs the agent, `GET /api/deals` lists stored deals.
- **App**: React + Vite. Search form → deal cards with price, airline, dates, duration, stops, and booking/source links.

## Setup

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 20+, and **one** LLM API key — [Anthropic](https://platform.claude.com/) or [OpenAI](https://platform.openai.com/api-keys).

### Backend

```sh
cd backend
cp .env.example .env        # then put your ANTHROPIC_API_KEY or OPENAI_API_KEY in .env
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### Frontend

```sh
cd frontend
npm install
npm run dev                 # opens on http://localhost:5173, proxies /api to :8000
```

Open http://localhost:5173, enter a route (e.g. `TLV` → `London`, "first two weeks of October"), and hit **Find deals**. A search takes a minute or two — the agent is running several live web searches.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/search` | `{origin, destination, dates_hint}` → runs the agent, stores + returns deals |
| `GET` | `/api/deals` | List stored deals. Filters: `origin`, `destination`, `max_price`, `limit` |
| `GET` | `/api/health` | Liveness check |

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — (one required) | LLM API access; provider auto-detected from the key |
| `FLIGHT_SCOUT_PROVIDER` | auto | Force `anthropic` or `openai` when both keys are set |
| `FLIGHT_SCOUT_MODEL` | `claude-opus-5` / `gpt-5.5` | Model powering the agent (per provider) |

## Roadmap

- [ ] **Notifications** — scheduled re-searches of saved routes (cron / LangGraph cron), push a notification (email / Telegram / ntfy) when a deal drops below a price threshold. The storage layer already keeps `found_at` history per route, so price-drop detection is a query away.
- [ ] Background searches (`POST /api/search` returns a job id instead of blocking)
- [ ] Price history chart per route
- [ ] De-duplication of identical deals across runs
