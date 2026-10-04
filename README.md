# ✈️ Flight Scout

An AI agent that hunts the live web for cheap flights, stores what it finds, and shows the results in a web app.

## How it works

![Flight Scout system architecture](./docs/architecture.png)

- **Search engine** (`engine.py`): routes each search to the best source. Exact routes and dates get **live Google Flights fares** via [SerpApi](https://serpapi.com); flexible dates use **Google Travel Explore** to find the cheapest dates, then confirm them live; regions, "anywhere" and fallbacks go to the LLM agent. A monthly live-search budget and a response cache keep it on SerpApi's free plan.
- **Agent**: [LangGraph](https://langchain-ai.github.io/langgraph/) (Python) driving an LLM with the provider's **server-side web search tool**. Works with **Anthropic** (Claude) or **OpenAI** (GPT), auto-detected from the API key. A *research* pass searches the live web, then a cheaper *extract* pass turns the findings into validated structured deals.
- **Cleanup**: every deal — API or agent — goes through one `postprocess` step: canonical airlines and airports, currency checks, the search criteria enforced in code, dedupe.
- **Storage**: [Turso](https://turso.tech) (hosted libSQL) in production, a local SQLite file in development — same code path, selected by env var. Jobs, usage counters, the provider cache and the alert log live there too.
- **API**: FastAPI — searches run as background jobs (persisted, restart-safe) with live progress over SSE; it also serves the built frontend, the Telegram bot webhook and the book-direct redirect.
- **App**: React + Vite with Search, Explore (map), History, Watches and Trends tabs.
- **Watches**: a daily GitHub Actions job runs the same watcher code directly against Turso, scores each watch against its price history, and sends one digest to Telegram/ntfy.

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
| `GET`            | `/api/jobs/{job_id}`                 | Poll a job: `{status: running\|done\|error\|cancelled\|interrupted, deals, summary, error, request}` — survives restarts (stored in the DB) |
| `GET`            | `/api/jobs/{job_id}/events`          | Live progress stream (SSE): the agent's web-search calls and streamed research text; `?after=N` skips events already seen      |
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
| `GET`            | `/api/usage`                         | Fare-API calls this month per provider: `{month, providers: [{provider, configured, used, budget, left}]}` (feeds the live-search meter) |
| `POST`           | `/api/explore`                       | Explore mode — cheapest destinations from an origin (Google Travel Explore, no destination): `{origin, month (0 = next 6 months), travel_duration: weekend\|week\|two_weeks, trip_type, max_stops, passengers, cabin_class, cabin_bag, currency, max_price?, region?, country?, cached_only?}` → `{destinations: [{city, country, region, airport, search_destination, lat, lon, thumbnail, price, price_per_person, depart_date, return_date, airline, airline_code, stops, duration, booking_links, ...}], cached, live_searches, left, fetched_at, expires_at}`. 1 live search, cached 6h; `max_price`/`region`/`country` are filtered after the fetch (free). 409 = budget used up, 503 = no SerpApi key, 404 = not cached (`cached_only`) |
| `POST`           | `/api/explore/quote`                 | What the same body would cost now: `{configured, cached, cost: 0\|1, left, budget}` — no network |
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

Watches additionally take `alerts` (see [Smart alerts](#smart-alerts); PATCH may send just the rule fields that change), and every watch returned by the API carries read-only `stats` (`runs`, `last_best`, `low_30d`, `typical`, `all_time_low`, `score`, in the watch currency, plus what they rest on: `basis`, `basis_runs`, `history_points`, Google's `typical_low`/`typical_high`, `legacy_runs`/`legacy`; `null` before its first run) and `last_alert`.

A finished search job also carries `meta`: `sources` used (`google_flights`, `aviasales`, `web`), Google's `price_insights` for the route (`typical_low`/`typical_high`, `price_level`), `live_searches` spent and any `messages` (e.g. the monthly live budget is used up).

Each returned deal carries IATA airports, local times, flight numbers, airline code, `price_confidence` (`verified` = exact dated fare seen, `estimated`, `from_price` = "from X" teaser), `source` + `seen_at` (where and when the price was seen), `booking_links` pre-filled with the search, and the `search_params` of the run that found it.




## Notifications

Watched routes are re-searched daily by the `watch-flights` [GitHub Actions workflow](.github/workflows/watch-flights.yml) (also runnable manually from the Actions tab). The sweep sends **one daily digest** — every watch's best price and deal score, with the watches whose [alert rules](#smart-alerts) fired listed first, each with its reasons and best booking link. A digest with nothing new is sent quietly (ntfy low priority, silent Telegram). **Run now** on a single watch alerts immediately, and only when a rule fires. Channels:

- **ntfy** (default, zero signup): set `NTFY_TOPIC` to a private topic name and subscribe to it in the [ntfy app](https://ntfy.sh/app) or at `https://ntfy.sh/<topic>`. Gets plain text; tapping opens the top deal's booking link.
- **Telegram** (optional): set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (comma-separate several chat IDs to alert more than one person). Gets HTML-formatted messages with inline booking links.

Since 2.3 the workflow runs the sweep **itself** (`backend/scripts/run_watches.py`, straight against Turso), so the Render app is never woken for it; the run still appears in the app as a `watch_run` job and counts against the same SerpApi budget. Each run's results table is on the workflow run's summary page.

- **Repo secrets** (Settings → Secrets and variables → Actions): `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`), `SERPAPI_API_KEY`, `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` and/or `NTFY_TOPIC`, plus `TRAVELPAYOUTS_TOKEN` (+ `TRAVELPAYOUTS_MARKER`) if Render has them. A key missing here but set on Render makes the sweep behave differently: without the Aviasales token, flexible watches pre-screen with Google Explore and spend more live searches.
- **Repo variables** (optional, non-secret): `FLIGHT_SCOUT_MODEL` (default `gpt-5.5`), `SERPAPI_MONTHLY_BUDGET` (default 230 — set it to Render's value), and `FLIGHT_SCOUT_PROVIDER` / `FLIGHT_SCOUT_EXTRACT_MODEL` / `FLIGHT_SCOUT_AI_BUDGET_USD` / `FLIGHT_SCOUT_PRICES_JSON` / `SERPAPI_RETURN_LEGS`, all mirroring the server env vars. `WATCH_SWEEP_MODE=render` switches the schedule back to the old path.
- **Manual runs / rollback:** Actions → *Flight watches* → *Run workflow*: `mode` = `direct` (default) or `render` (the old path: wake the Render app, `POST /api/watches/run`, poll the job; that path needs `CRON_SECRET` to match the server's `CRON_SECRET` env var), and `notify` (off = check without sending a digest). Locally: `cd backend && uv run python scripts/run_watches.py [--no-notify] [--strict]`. It targets Turso if `backend/.env` has the Turso vars, so it **spends real credits**.
- **Exit status:** the step fails if the run errors, every watch fails, or the digest can't be delivered to a configured channel. A single failed watch only shows as a warning.

### Smart alerts

Each watch has its own rules (editable under **Alert me when** in the watch editor, or `alerts` in the API). All are on by default; every comparison uses the watch's own earlier runs of the same fare kind (party, cabin, trip type), converted into the watch currency.

| Rule | Field(s) | Default | Fires when the run's best price… |
| ---- | -------- | ------- | -------------------------------- |
| Threshold | `threshold` | on | is at or under `max_price` |
| New low | `new_low`, `new_low_days` | on, 30 days | is lower than every run in the last N days (needs 5+ runs in that window) |
| Under typical | `under_typical`, `under_typical_pct` | on, 20% | is that far under the median of the last 30 days' run-bests (needs 3+ runs) |
| Big drop | `big_drop`, `big_drop_pct` | on, 15% | is that much cheaper than the previous run |

**Anti-spam:** the same fare (airline, airports, dates, flight numbers, party) is not re-alerted within 7 days unless it dropped another 5%; sent alerts are logged in the `alert_log` table (`GET /api/alerts`). **Deal score** (0–100, in messages, the bot and on watch cards): 75% percentile against earlier run-bests (ties count half) + 25% distance from their median; 50 = a typical price, 88+ = a new low; shown after 3 earlier runs.

**Trustworthy stats:** only one trust tier feeds the numbers, scores and smart rules (`stats.basis`): `verified` (the watch's own Google Flights runs, once it has 5+), `google` (Google's price insights from the latest live search — typical = middle of Google's typical range, lows from its price history — plus any verified runs), `explore` (Google Travel Explore prices — Google's cheapest-dates price, not a confirmed fare — only while there is no verified data), `cached` (Aviasales runs, only while there is no verified data) or `legacy` (unverified LLM-era estimates only — the old behaviour, labelled). Legacy runs never mix into a trusted basis; watch cards show them only in a muted note. Messages say when the basis is Google ("typical $285–390 per Google"). Google's history is for the one date pair the live search asked about, so it is an approximation for a multi-date watch until it has its own verified runs.

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
| `FLIGHT_SCOUT_MODEL`                      | `claude-opus-5` / `gpt-5.4-mini` | Research-pass model (web search, per provider); production runs `gpt-5.5` for accuracy |
| `FLIGHT_SCOUT_EXTRACT_MODEL`              | `claude-haiku-4-5` / `gpt-5.4-mini` | Extract-pass model (structured output only, no tools). The cheap tier is ~6x cheaper than `gpt-5.5`; it may occasionally drop a deal from awkward notes, but `postprocess()` still enforces every criterion. Set it to the research model to undo |
| `FLIGHT_SCOUT_AI_BUDGET_USD`              | — (no cap)                       | Monthly AI spend cap (estimated, UTC month). Warns at 80%; once reached, web (LLM) searches are refused with a message — fare-API searches still work |
| `FLIGHT_SCOUT_PRICES_JSON`                | built-in table                   | Override LLM list prices, e.g. `{"models": {"gpt-5.5": {"input": 5, "cached_input": 0.5, "output": 30}}, "web_search_per_1k": {"openai": 10}}` (USD per 1M tokens / per 1k searches). Defaults live in `backend/app/llm_usage.py` |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | —                                | Hosted DB ([Turso](https://turso.tech)); falls back to local SQLite when unset |
| `STATIC_DIR`                              | `frontend/dist`                  | Directory of built frontend for FastAPI to serve                               |
| `APP_PASSWORD`                            | — (open when unset)              | Locks the app/API behind a shared password (UI shows a lock screen)            |
| `CRON_SECRET`                             | —                                | Shared secret for `POST /api/watches/run` (set the same value in GitHub Actions) |
| `NTFY_TOPIC`                              | —                                | ntfy.sh topic for price-drop alerts                                            |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | —                                | Telegram bot alerts; chat ID accepts a comma-separated list of recipients (also the bot's allow-list) |
| `TELEGRAM_WEBHOOK_SECRET`                 | — (bot off)                      | Enables the two-way bot: webhook path segment + `secret_token`, 16+ chars `A-Za-z0-9_-` |
| `PUBLIC_URL`                              | request host                     | Public https base URL `POST /api/telegram/setup` registers                     |
| `SERPAPI_API_KEY`                         | — (live fares off)               | [SerpApi](https://serpapi.com) Google Flights: live, exact dated fares (free plan 250 searches/month) |
| `SERPAPI_MONTHLY_BUDGET`                  | `230`                            | Live searches allowed per calendar month (UTC) before falling back to cached fares / the web |
| `SERPAPI_RETURN_LEGS`                     | `0`                              | Also fetch return-flight times for the N cheapest round trips (1 extra search each) |
| `TRAVELPAYOUTS_TOKEN`                     | — (cached fares off)             | [Travelpayouts](https://www.travelpayouts.com) Aviasales Data API: free cached fares, calendars, explore |
| `TRAVELPAYOUTS_MARKER`                    | —                                | Partner marker appended to Aviasales booking links                             |




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

Free-tier notes:

- **Sleep / cold start.** The service spins down after ~15 min idle; the next request takes ~1 min while it boots. The page itself comes from that same server, so a *first* visit to a sleeping instance shows the browser's own loading state for that minute — nothing can render before the server answers. Return visits are covered: a small service worker (`frontend/public/sw.js`) caches the app shell, so the page renders at once and shows a "Waking up the server…" banner (elapsed time, rough progress) until `/api/health` answers, then carries on by itself. The same banner appears if you start a search after the tab sat idle long enough for the server to sleep.
- **No keep-alive pinger, on purpose.** Render's free tier gives 750 instance-hours a month and one always-on instance uses ~744, so pinging it awake is technically possible but spends the whole allowance on idle time (and any second free service on the account would run out). Nor does the daily watch sweep need it awake: it runs in GitHub Actions against Turso.
- **Restarts.** Every deploy or env-var change restarts the instance. Jobs are stored in the DB, so a search or watch run in flight comes back as **interrupted** (never re-run automatically — each run can cost a live fare-API credit or LLM money); the UI offers *Run again* with the same criteria. Reloading the page mid-search re-attaches to it, and a finished result is shown even if the tab was closed.

## Features

- **Live web search** for deals on any route — or an explore-style search to a country, region or "anywhere" — with the agent's searches and findings **streamed live** into the UI while it works
- **Search-engine criteria**: one-way or round trip, party size (adults/children/infants, prices are party totals), cabin class, max stops, a departure window and trip length, a price ceiling, preferred currency, and nearby airports. The agent prefers exact dated fares from Google Flights/Skyscanner/Kayak/airline sites (opening pages to confirm them), and every result is re-checked in code against the criteria (stops, budget, dates, trip length, no past departures), de-duplicated and sorted by price
- **Real fare data** (optional keys): live, exact Google Flights fares via SerpApi for exact routes and dates; the free Aviasales cache picks the cheapest dates for flexible searches (then the top ones are confirmed live) and seeds region/"anywhere" searches — without it, Google Travel Explore picks the dates (≤ 3 live searches per flexible search, ~2 typical); every card says where its price came from ("Google Flights · live", "Google Explore · estimate", "Aviasales · cached 2d", "Web search"); watch cards show their live-search cost and the Watches tab warns when active watches would exceed the monthly budget, results show Google's typical price range for the route, and the Search tab shows live searches used this month. Without keys the LLM agent works exactly as before
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