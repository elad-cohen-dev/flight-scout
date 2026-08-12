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
| `POST` | `/api/search` | `{origin, destination, dates_hint}` → starts a search job, returns `{job_id}` (202) |
| `GET` | `/api/jobs/{job_id}` | Poll a job: `{status: running\|done\|error, deals, summary, error}` |
| `GET` | `/api/deals` | List stored deals. Filters: `origin`, `destination`, `max_price`, `limit` |
| `GET` | `/api/history` | Best price per route per search run (feeds the price chart) |
| `POST` | `/api/watches` | Create a price watch: `{origin, destination, dates_hint, max_price}` |
| `GET` / `DELETE` | `/api/watches` / `/api/watches/{id}` | List / delete watches |
| `POST` | `/api/watches/run` | Re-search all watches, alert on threshold hits (requires `X-Cron-Secret` header) |
| `GET` | `/api/health` | Liveness check |

## Notifications

Watched routes are re-searched daily by the [`watch-flights` GitHub Actions workflow](.github/workflows/watch-flights.yml) (also runnable manually from the Actions tab). When a deal is found at or below a watch's `max_price`, a push notification is sent via every configured channel:

- **ntfy** (default, zero signup): set `NTFY_TOPIC` to a private topic name and subscribe to it in the [ntfy app](https://ntfy.sh/app) or at `https://ntfy.sh/<topic>`.
- **Telegram** (optional): set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

The workflow authenticates with the `CRON_SECRET` repo secret, which must match the `CRON_SECRET` env var on the server.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — (one required) | LLM API access; provider auto-detected from the key |
| `FLIGHT_SCOUT_PROVIDER` | auto | Force `anthropic` or `openai` when both keys are set |
| `FLIGHT_SCOUT_MODEL` | `claude-opus-5` / `gpt-5.4-mini` | Model powering the agent (per provider) |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | — | Hosted DB ([Turso](https://turso.tech)); falls back to local SQLite when unset |
| `STATIC_DIR` | `frontend/dist` | Directory of built frontend for FastAPI to serve |

## Deployment (Render free tier + Turso free tier)

The repo ships a `Dockerfile` (builds the frontend, serves everything from FastAPI on one port) and a `render.yaml` blueprint.

1. **Turso** (persistent DB — Render's free disk is ephemeral):
   - Sign up at https://turso.tech, create a database.
   - Grab the database URL (`libsql://...`) and create an auth token.
2. **Render**:
   - Sign up at https://render.com and connect your GitHub.
   - New → Blueprint → select this repo; Render reads `render.yaml`.
   - Fill in the env vars: `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`), `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.
3. Every push to `main` auto-deploys.

Free-tier notes: the service spins down after ~15 min idle (first request takes ~1 min to cold-start), and searches run as background jobs the UI polls — so keep the tab open until a search finishes.

## Roadmap

- [x] Notifications — daily scheduled re-searches of watched routes, push alert on price drops (GitHub Actions cron + ntfy/Telegram)
- [x] Background searches (`POST /api/search` returns a job id instead of blocking)
- [x] Price history chart per route
- [ ] De-duplication of identical deals across runs
- [ ] Access control (each search spends LLM credit)
