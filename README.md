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
| `PATCH`          | `/api/watches/{id}`                  | Edit a watch: any subset of `dates_hint`, `max_price`, `currency`, the search criteria, and `active` (`false` pauses, `true` resumes). The route can't change; changing criteria resets `last_best_price` |
| `POST`           | `/api/watches/{id}/run`              | Run one watch immediately (same check + alert logic as the daily sweep), returns a job                                             |
| `POST`           | `/api/watches/run`                   | Re-search all watches and send one daily digest (requires `X-Cron-Secret` header)                                                  |
| `GET`            | `/api/alerts`                        | Sent alerts, newest first (`watch_id`, `limit` filters) — the anti-spam log                                                        |
| `POST`           | `/api/telegram/webhook/{secret}`     | Telegram bot webhook (secret path + `X-Telegram-Bot-Api-Secret-Token`; only `TELEGRAM_CHAT_ID` chats are answered)                 |
| `POST`           | `/api/telegram/setup`                | Register this deployment as the bot's webhook + command menu (`{base_url}` optional; defaults to `PUBLIC_URL` / the request host)  |
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

Watches additionally take `alerts` (see [Smart alerts](#smart-alerts); PATCH may send just the rule fields that change), and every watch returned by the API carries read-only `stats` (`runs`, `last_best`, `low_30d`, `typical`, `all_time_low`, `score`, in the watch currency; `null` before its first run) and `last_alert`.

Each returned deal carries IATA airports, local times, flight numbers, airline code, `price_confidence` (`verified` = exact dated fare seen, `estimated`, `from_price` = "from X" teaser), `booking_links` pre-filled with the search, and the `search_params` of the run that found it.




## Notifications

Watched routes are re-searched daily by the `watch-flights` [GitHub Actions workflow](.github/workflows/watch-flights.yml) (also runnable manually from the Actions tab). The sweep sends **one daily digest** — every watch's best price and deal score, with the watches whose [alert rules](#smart-alerts) fired listed first, each with its reasons and best booking link. A digest with nothing new is sent quietly (ntfy low priority, silent Telegram). **Run now** on a single watch alerts immediately, and only when a rule fires. Channels:

- **ntfy** (default, zero signup): set `NTFY_TOPIC` to a private topic name and subscribe to it in the [ntfy app](https://ntfy.sh/app) or at `https://ntfy.sh/<topic>`. Gets plain text; tapping opens the top deal's booking link.
- **Telegram** (optional): set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (comma-separate several chat IDs to alert more than one person). Gets HTML-formatted messages with inline booking links.

The workflow authenticates with the `CRON_SECRET` repo secret, which must match the `CRON_SECRET` env var on the server.

### Smart alerts

Each watch has its own rules (editable under **Alert me when** in the watch editor, or `alerts` in the API). All are on by default; every comparison uses the watch's own earlier runs of the same fare kind (party, cabin, trip type), converted into the watch currency.

| Rule | Field(s) | Default | Fires when the run's best price… |
| ---- | -------- | ------- | -------------------------------- |
| Threshold | `threshold` | on | is at or under `max_price` |
| New low | `new_low`, `new_low_days` | on, 30 days | is lower than every run in the last N days (needs 5+ runs in that window) |
| Under typical | `under_typical`, `under_typical_pct` | on, 20% | is that far under the median of the last 30 days' run-bests (needs 3+ runs) |
| Big drop | `big_drop`, `big_drop_pct` | on, 15% | is that much cheaper than the previous run |

**Anti-spam:** the same fare (airline, airports, dates, flight numbers, party) is not re-alerted within 7 days unless it dropped another 5%; sent alerts are logged in the `alert_log` table (`GET /api/alerts`). **Deal score** (0–100, in messages, the bot and on watch cards): 75% percentile against earlier run-bests (ties count half) + 25% distance from their median; 50 = a typical price, 88+ = a new low; shown after 3 earlier runs.

### Telegram bot

With a bot token, the same bot also takes commands — via a webhook, so nothing polls and it works on Render's free tier (a message wakes a sleeping instance; Telegram retries if the first delivery times out):

| Command | Does |
| ------- | ---- |
| `/watches` | List watches with last best price, deal score, paused state |
| `/best 3` | Watch #3's cheapest current fare with its booking link |
| `/run 3` | Check watch #3 now; the bot replies again when the run finishes |
| `/pause 3` · `/resume 3` | Pause / resume a watch |
| `/watch TLV PRG October under 300 direct 2 adults` | Create a watch. Also understands `under €250` / `300 ils`, `max 1 stop`, `1 child`, `one-way`, `business`, `cabin bag`, `checked bag`, `nearby`, `"New York"` (quoted multi-word places); whatever's left is the dates text. A bare price is USD; give a currency as a symbol or code before or after the number (`under ₪900`, `under 300 ILS`, `under eur 250`) |
| `/help` | Command list |

Setup:

1. Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (the only chats the bot answers) and `TELEGRAM_WEBHOOK_SECRET` (16+ chars of `A-Z a-z 0-9 _ -`, e.g. `openssl rand -hex 24`) on the server.
2. Register the webhook once per URL, either from the deployed app — `curl -X POST https://<app>/api/telegram/setup -H "Authorization: Bearer $APP_PASSWORD"` — or from a checkout with the same env vars: `cd backend && uv run python -m app.telegram_bot https://<app>`. This calls Telegram's `setWebhook` with `https://<app>/api/telegram/webhook/<secret>` plus the same secret as `secret_token`, and publishes the command menu.

Requests to the webhook need both the secret path and Telegram's `X-Telegram-Bot-Api-Secret-Token` header; messages from other chats are acknowledged and ignored.

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
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | —                                | Telegram bot alerts; chat ID accepts a comma-separated list of recipients (also the bot's allow-list) |
| `TELEGRAM_WEBHOOK_SECRET`                 | — (bot off)                      | Enables the two-way bot: webhook path segment + `secret_token`, 16+ chars `A-Za-z0-9_-` |
| `PUBLIC_URL`                              | request host                     | Public https base URL `POST /api/telegram/setup` registers                     |




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
- **City & airport autocomplete** — ~550 airports, metro areas ("London — all airports"), countries and regions, accent-insensitive with Hebrew names; free text like "Greece or Cyprus" still works
- **Price calendar** — best fare per departure date for each route (compared in one currency, stale sightings faded); tap a day to see its fares or pre-fill a search around it
- **Price watches** — create one from any History card, edit or pause it anytime; routes re-searched daily (GitHub Actions cron) with currency-aware comparison (a ₪ fare is converted before checking a $ threshold)
- **Smart alerts** — beyond your threshold: a new N-day low, X% under the typical price, a big drop since the last check; a 0–100 deal score; no repeat alerts for the same fare within a week; one clean daily digest via ntfy/Telegram; watch cards show last best, 30-day low, typical price, score and the last alert
- **Telegram bot** — `/watches`, `/best`, `/run`, `/pause`, `/resume` and `/watch TLV PRG October under 300 direct` from your phone, over a webhook
- **Price-history chart** per route, built from every search run
- **De-duplication** of identical deals across runs ("seen 3× since Aug 10")
- **Access control** — optional shared password (`APP_PASSWORD`) with a lock screen
- **Tabbed app** — Search / History / Watches / Trends; history folds repeated runs of a search into one card with its price trend, grouped by date or route, with filter, sort and paging
- **Mobile-friendly** responsive UI with a bottom tab bar, and dark mode