# ✈️ Flight Scout

An AI agent that hunts the live web for cheap flights, stores what it finds, and shows the results in a web app.

## How it works

![Flight Scout system architecture](./docs/architecture.png)

- **Agent**: [LangGraph](https://langchain-ai.github.io/langgraph/) (Python) driving an LLM with the provider's **server-side web search tool** — no scraping infrastructure or flight-API keys needed. Works with **Anthropic** (Claude) or **OpenAI** (GPT); the provider is auto-detected from whichever API key you set. Two-node graph: a *research* pass that searches the live web, then an *extract* pass that turns the findings into validated structured deals.
- **Storage**: [Turso](https://turso.tech) (hosted libSQL) in production, a local SQLite file in development — same code path, selected by env var.
- **API**: FastAPI — searches run as background jobs the UI polls; the built frontend is served from the same port.
- **App**: React + Vite. Search form → grouped deal cards (price, airline, dates, duration, stops, booking links), price-history chart per route, price watches with daily re-checks.

**→ See [ARCHITECTURE.md](ARCHITECTURE.md) for the full end-to-end design: agent internals, request flow, storage/caching, the cron + notification pipeline, and the free-tier trade-offs.**



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

Open [http://localhost:5173](http://localhost:5173), enter a route (e.g. `TLV` → `London`, "first two weeks of October"), and hit **Find deals**. A search takes a minute or two — the agent is running several live web searches.

## API


| Method           | Path                                 | Description                                                                                                                        |
| ---------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `POST`           | `/api/search`                        | `{origin, destination, dates_hint, ...search params}` (see below) → starts a search job, returns `{job_id}` (202)                 |
| `GET`            | `/api/jobs/{job_id}`                 | Poll a job: `{status: running\|done\|error, deals, summary, error}`                                                                  |
| `GET`            | `/api/jobs/{job_id}/events`          | Live progress stream (SSE): the agent's web-search calls and streamed research text                                                |
| `POST`           | `/api/jobs/{job_id}/cancel`          | Stop a running job; it aborts at its next progress event without saving anything                                                   |
| `GET`            | `/api/deals`                         | List stored deals, de-duplicated across runs (`dedupe=false` for raw rows). Filters: `origin`, `destination`, `max_price`, `limit` |
| `GET`            | `/api/history`                       | Best price per route per search run (feeds the price chart)                                                                        |
| `POST`           | `/api/watches`                       | Create a price watch: `{origin, destination, dates_hint, max_price, currency, ...search params}` — `max_price` is the alert threshold |
| `GET` / `DELETE` | `/api/watches` / `/api/watches/{id}` | List / delete watches                                                                                                              |
| `POST`           | `/api/watches/{id}/run`              | Run one watch immediately (same check + alert logic as the daily sweep), returns a job                                             |
| `POST`           | `/api/watches/run`                   | Re-search all watches, alert on threshold hits (requires `X-Cron-Secret` header)                                                   |
| `GET`            | `/api/health`                        | Liveness check                                                                                                                     |

### Search params

Searches and watches accept the same criteria. All are optional; the defaults reproduce a plain 1-adult economy round trip.

| Field | Default | Meaning |
| ----- | ------- | ------- |
| `origin` / `destination` | — | City, IATA code or comma list; the destination may also be a country/region (`Greece`) or `anywhere` |
| `dates_hint` | `anytime in the next 3 months` | Free-text date preference |
| `trip_type` | `round_trip` | `round_trip` or `one_way` |
| `passengers` | `{adults: 1, children: 0, infants: 0}` | Party size; prices are the **total for the whole party** (`price_per_person` is also returned) |
| `cabin_class` | `economy` | `economy`, `premium_economy`, `business`, `first` |
| `max_stops` | `null` (any) | `0` = direct only (legacy `direct_only: true` still works), `1`, `2`... |
| `depart_from` / `depart_to` | — | Departure window (ISO dates), enforced on results |
| `nights_min` / `nights_max` | — | Trip length for round trips, enforced on results |
| `max_price` | — | Price ceiling for the whole party in `currency` (converted before comparing) |
| `currency` | `USD` | Preferred quote currency |
| `cabin_bag` / `checked_bag` | `false` | Prices must include a 10kg cabin trolley / a 20kg+ checked bag per traveller |
| `nearby_airports` | `false` | Also consider alternate airports near origin and destination |

Each returned deal carries IATA airports, local times, flight numbers, airline code, `price_confidence` (`verified` = exact dated fare seen, `estimated`, `from_price` = "from X" teaser), `booking_links` pre-filled with the search, and the `search_params` of the run that found it.




## Notifications

Watched routes are re-searched daily by the `watch-flights` [GitHub Actions workflow](.github/workflows/watch-flights.yml) (also runnable manually from the Actions tab). When a deal is found at or below a watch's `max_price`, a push notification is sent via every configured channel:

- **ntfy** (default, zero signup): set `NTFY_TOPIC` to a private topic name and subscribe to it in the [ntfy app](https://ntfy.sh/app) or at `https://ntfy.sh/<topic>`.
- **Telegram** (optional): set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (comma-separate several chat IDs to alert more than one person).

The workflow authenticates with the `CRON_SECRET` repo secret, which must match the `CRON_SECRET` env var on the server.

## Configuration


| Env var                                   | Default                          | Purpose                                                                        |
| ----------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------ |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`    | — (one required)                 | LLM API access; provider auto-detected from the key                            |
| `FLIGHT_SCOUT_PROVIDER`                   | auto                             | Force `anthropic` or `openai` when both keys are set                           |
| `FLIGHT_SCOUT_MODEL`                      | `claude-opus-5` / `gpt-5.4-mini` | Model powering the agent (per provider); production runs `gpt-5.5` for accuracy |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | —                                | Hosted DB ([Turso](https://turso.tech)); falls back to local SQLite when unset |
| `STATIC_DIR`                              | `frontend/dist`                  | Directory of built frontend for FastAPI to serve                               |
| `APP_PASSWORD`                            | — (open when unset)              | Locks the app/API behind a shared password (UI shows a lock screen)            |
| `CRON_SECRET`                             | —                                | Shared secret for `POST /api/watches/run` (set the same value in GitHub Actions) |
| `NTFY_TOPIC`                              | —                                | ntfy.sh topic for price-drop alerts                                            |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | —                                | Telegram bot alerts; chat ID accepts a comma-separated list of recipients      |




## Deployment (Render free tier + Turso free tier)

The repo ships a `Dockerfile` (builds the frontend, serves everything from FastAPI on one port) and a `render.yaml` blueprint.

1. **Turso** (persistent DB — Render's free disk is ephemeral):
  - Sign up at [https://turso.tech](https://turso.tech), create a database.
  - Grab the database URL (`libsql://...`) and create an auth token.
2. **Render**:
  - Sign up at [https://render.com](https://render.com) and connect your GitHub.
  - New → Blueprint → select this repo; Render reads `render.yaml`.
  - Fill in the env vars: `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`), `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.
3. Every push to `main` auto-deploys.

Free-tier notes: the service spins down after ~15 min idle (first request takes ~1 min to cold-start), and searches run as background jobs the UI polls — so keep the tab open until a search finishes.

## Features

- **Live web search** for deals on any route — or an explore-style search to a country, region or "anywhere" — with the agent's searches and findings **streamed live** into the UI while it works
- **Search-engine criteria**: one-way or round trip, party size (adults/children/infants, prices are party totals), cabin class, max stops, a departure window and trip length, a price ceiling, preferred currency, and nearby airports. The agent prefers exact dated fares from Google Flights/Skyscanner/Kayak/airline sites (opening pages to confirm them), and every result is re-checked in code against the criteria (stops, budget, dates, trip length, no past departures), de-duplicated and sorted by price
- **Price confidence** on every deal: verified dated fare vs. estimate vs. "from" teaser
- **One-click, pre-filled booking** — airline deep links (Ryanair, Wizz Air, easyJet) and Google Flights / Skyscanner / Kayak / Momondo links with the route, exact dates, passengers, cabin and (where supported) stops and bags already set; the card shows what's pre-filled and what's left to set at checkout
- **Baggage-inclusive pricing** — optionally require a 🎒 cabin trolley and/or 🧳 20kg+ checked bag; the agent adds the airline's bag fees to advertised fares (or flags when it can't verify them), so comparisons reflect what the trip actually costs
- **Price watches** — routes re-searched daily (GitHub Actions cron), push alert via ntfy/Telegram when a deal drops below your threshold, with currency-aware comparison (a ₪ fare is converted before checking a $ threshold)
- **Price-history chart** per route, built from every search run
- **De-duplication** of identical deals across runs ("seen 3× since Aug 10")
- **Access control** — optional shared password (`APP_PASSWORD`) with a lock screen
- **Tabbed app** — Search / History / Watches / Trends; history folds repeated runs of a search into one card with its price trend, grouped by date or route, with filter, sort and paging
- **Mobile-friendly** responsive UI with a bottom tab bar, and dark mode