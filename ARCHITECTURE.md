# Flight Scout — Architecture

End-to-end: a LangGraph agent that searches the live web for flight deals, a FastAPI backend that runs it as background jobs and serves the React UI, a Turso (libSQL) database, and a GitHub Actions cron that re-checks watched routes daily and pushes alerts.

## System overview

![Flight Scout system architecture](./docs/architecture.png)

*Diagram source: [docs/architecture.excalidraw](./docs/architecture.excalidraw) — open it at [excalidraw.com](https://excalidraw.com) to edit.*

Everything server-side lives in **one process** on one free Render instance: the API, the agent, the watcher, and the static frontend. There is no queue, no worker fleet, no scraping infrastructure — the design goal is the smallest system that does the job on free tiers.

## The LangGraph agent (`backend/app/agent.py`)

The agent is a two-node `StateGraph`:

```
START ──▶ research ──▶ extract ──▶ END
          (LLM + server-side       (structured-output pass →
           web search → prose)      validated FlightDeal list)
```

State flowing through the graph (`AgentState`):

```python
origin: str            # "TLV" (or a comma list)
destination: str       # "Prague", a country/region ("Greece"), a comma list, or "anywhere"
dates_hint: str        # free text: "October 11 week", "a weekend in November"
params: SearchParams   # structured criteria: trip type, passengers, cabin class, max stops,
                       # date window + trip length, bags, max price, currency, nearby airports
research_notes: str    # output of node 1, input of node 2
deals: list[FlightDeal]  # final output
```

`SearchParams` (in `models.py`) is the one contract shared by the agent, storage, booking links and the UI. It is persisted with every stored deal, so re-runs and booking links reproduce the exact party and criteria.

### Node 1 — `research`

One LLM call with the provider's **server-side web search tool** bound. The searching loop (issue queries, fetch pages, read results) runs entirely on the provider's infrastructure and the node gets back a single prose answer — no client-side tool loop, no scraper, no flight-API keys:

- **Anthropic**: `web_search_20260209` (10 uses, 14 for flexible/region searches) plus `web_fetch_20260209` (5 uses) to open result and airline pages and confirm exact dated fares. A long server-side tool loop that pauses (`stop_reason: pause_turn`) is continued up to twice.
- **OpenAI**: `{"type": "web_search"}` — requires the Responses API (`ChatOpenAI(use_responses_api=True)`)

The prompt is built from every `SearchParams` field plus today's date. It asks for exact dated fares from Google Flights, Skyscanner, Kayak, Momondo and airline sites over "from" teasers, and a fixed report format per deal: IATA airports, times, flight numbers, fare for the whole party with the per-person breakdown, and bag fees added when bags are required. Region/"anywhere" destinations become an explore search across several airports.

### Node 2 — `extract`

A second, cheaper LLM pass over the research notes using LangChain's `with_structured_output(FlightDealList)` — the model is forced to emit the Pydantic schema, so the output is validated data, not parsed prose. Two safety nets sit on top of the model:

- **Criteria are enforced in code** (`postprocess()`): max stops, max price (after currency conversion), departure window, trip length, one-way vs round trip, no past dates, no non-positive prices. On a round-trip search, a fare that is clearly one-way is doubled and marked `estimated`, before the budget check. Within-run duplicates are merged (best `price_confidence`, then cheapest) and results are sorted by price in the preferred currency. The prompt asks; the code guarantees. Deals that can't be checked (free-text dates, unknown currency) are kept.
- **Honest confidence**: every deal carries `price_confidence` — `verified` (exact fare seen for these dates), `estimated` (computed, e.g. fare + bag fee), or `from_price` (a "from X" teaser).
- **Currency codes are normalized by a Pydantic validator** on `FlightDeal` (`"₪"` → `ILS`, `"$"` → `USD`, ...). The same model validates rows read back from the DB, so old mis-coded rows heal on read.

### Why two passes?

A single call can't reliably do both jobs: web search needs a free-form response the model can reason in, while storage needs strict schema. Splitting them also means the second pass can run on the same cheap model with no tools bound. A full search costs **two model calls**; cost depends mostly on the model (`FLIGHT_SCOUT_MODEL`, production runs `gpt-5.5`).

### Provider selection

`_provider()` picks the provider at call time: `FLIGHT_SCOUT_PROVIDER` env override → `ANTHROPIC_API_KEY` present → `OPENAI_API_KEY` present. Default models: `claude-opus-5` / `gpt-5.4-mini`, overridable with `FLIGHT_SCOUT_MODEL`.

## Request flow — what happens on "Find deals"

A live web search takes 1–2 minutes, far beyond sane HTTP timeouts (and Render's proxy limits), so searches run as **background jobs**:

1. The UI sends `POST /api/search {origin, destination, dates_hint, ...SearchParams}`.
2. FastAPI spawns a daemon thread and immediately returns `202 {job_id, status: running}`.
3. The thread runs the agent: *research* (server-side web search → prose findings), then *extract* (structured output → `FlightDeal` list), then inserts the deals into the DB.
4. Meanwhile the UI polls `GET /api/jobs/{job_id}` every 3 seconds until the status flips to `done` (response carries the saved deals) or `error`.
5. In parallel, the UI follows `GET /api/jobs/{job_id}/events` — a Server-Sent Events stream of the agent's live progress. The research pass runs with `llm.stream()`, so every web-search call the model issues and every chunk of its findings is pushed to an in-memory event buffer and rendered in the UI as a streaming "agent console" (the LLM-chat feel instead of a frozen spinner). The stream is best-effort: polling remains the source of truth for completion, so a dropped stream costs only the live view.

Jobs live in an in-memory dict guarded by a lock — fine for a single instance; a restart loses in-flight jobs but never stored data. The same mechanism runs watch sweeps (`kind: watch_run`).

## Storage (`backend/app/storage.py`)

Two interchangeable backends behind one module — both speak the sqlite3 DB-API:

| | Local dev | Production |
|---|---|---|
| Backend | SQLite file (`backend/flight_scout.db`) | [Turso](https://turso.tech) hosted libSQL |
| Selected by | `TURSO_DATABASE_URL` unset | `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` set |
| Why | zero setup | Render free tier has an **ephemeral disk** — a local file would vanish on every deploy/restart |

Three tables: `deals` (every deal from every run, append-only), `watches` (alert rules in an `alert_settings` JSON column) and `alert_log` (every alert sent: watch, fare fingerprint, price, rules, score, time). Schema is created on first connect; lightweight migrations are `ALTER TABLE` statements that fail harmlessly once applied.

Performance shape (matters because every Turso statement is a network round trip):

- **One pooled connection per process**, serialized by a lock (writes happen on job threads while requests read). Schema DDL runs once per process, not per request. A dropped idle connection is detected and retried once on a fresh connection — note that libsql surfaces dead-connection errors as a `BaseException`-derived pyo3 panic, which the retry deliberately catches.
- **60-second in-process read cache** over deals/watches/history, invalidated wholesale by every write — so the UI sees fresh data the instant a search or watch run lands. Before this, a page load cost ~24 Turso round trips (~5s); warm loads now cost zero.

Read-time shaping (the DB stays raw; views are computed):

- **De-duplication**: identical deals re-found across runs (same route/airline/dates/price) collapse into one row carrying `times_seen` and `first_found_at`. A *changed* price is deliberately a new entry — repricing is information.
- **Price history**: deals group by the *requested* route parsed from the search query (extracted deals name airports LTN/LHR/London inconsistently), each run contributes its minimum price, and every price is **converted to the route's dominant currency** first — runs quote whatever currency the source pages used.

## Booking links (`backend/app/booking.py`)

`build_links(deal, params)` turns a deal plus its stored `SearchParams` into ranked, pre-filled purchase links. It is pure (no network), never raises, and runs at read time, so legacy rows get links too (assumed 1 adult, economy).

- **Airline deep links** where the carrier's site accepts URL parameters (Ryanair, Wizz Air, easyJet): route, exact dates, adults/children/infants. Other carriers (El Al, Israir, Arkia, Aegean, Lufthansa group, Turkish, …) get their booking home page.
- **Meta-search**: Google Flights (`?q=` natural-language query incl. passengers, cabin, nonstop), Skyscanner, Kayak and Momondo. Kayak/Momondo also take bag filters, so their prices include bag fees.
- **Ranking**: pre-filled airline link → Google Flights → the agent's own specific booking URL → other meta-search → airline home pages. Each link lists what it actually pre-fills; the UI shows that and warns about anything left to set on the site ("Add 20kg bag at checkout").
- Dated links need exact dates; month text falls back to month views. Metro codes (LON, PAR…) work on meta-search only; airline engines need a real airport.

## Web app (`frontend/`)

React + Vite, four hash-routed tabs so refresh/back work: **Search** (full criteria form + live agent console + this run's results), **History** (repeated runs of the same search folded into one card with price trend, grouped by date or route, filter/sort/paging), **Watches**, and **Trends** (price chart per route). Top tabs on desktop, a bottom bar on phones, dark mode via `prefers-color-scheme`. History fetches raw sightings (`/api/deals?dedupe=false`) so run-to-run price changes are exact.

## Watches, cron, and notifications

Every morning (05:00 UTC), the GitHub Actions workflow:

1. **Wakes the app** — a retry loop on `GET /api/health`, because the free Render instance sleeps after ~15 idle minutes.
2. **Triggers the sweep** — `POST /api/watches/run` with the `X-Cron-Secret` header; the API returns `202 {job_id}` and runs the watcher in a background thread.
3. For **each active watch**, the watcher re-runs the agent with the watch's stored `SearchParams`, saves the results, converts every deal price into the watch's currency, and scores the run's best price against the watch's history (`app/alerts.py`): its earlier run-bests of the same fare kind (party, cabin, trip type) give the 30-day low, the typical price (30-day median), the all-time low and a 0–100 deal score, and the alert rules fire on the threshold, a new N-day low, X% under typical, or a big drop vs the previous run. A fare alerted in the last 7 days is suppressed unless it fell another 5% (`alert_log` table). After the last watch, **one digest** goes out — hits first, each with its reasons and the most pre-filled booking link, then a one-liner per other watch. A single-watch "Run now" instead alerts immediately when a rule fires.
4. **Polls the job** every 30 seconds until done (which conveniently also keeps the instance awake for the duration).

- The **threshold comparison is currency-aware** (`app/currency.py`): deal prices convert into the watch's currency using ECB rates from frankfurter.dev (keyless, cached in-process for 12h) with a static fallback table. Raw-number comparison was the original design and it failed both ways: ₪1,009 vs a $300 threshold never alerted; £95 vs a $100 threshold would have alerted wrongly.
- Alerts go to **every configured channel**: ntfy (plain text, topic-based, zero signup) and/or a Telegram bot (HTML).
- The **Telegram bot** is two-way over a webhook (`app/telegram_bot.py`), not polling, so it costs nothing while idle on the free tier. `POST /api/telegram/webhook/{secret}` checks the secret path and Telegram's secret-token header, answers only `TELEGRAM_CHAT_ID` chats, and returns its reply *as the webhook response* (Telegram executes a `sendMessage` body), so most commands make no outbound call. `/run` starts an ordinary watch-run job with notifications off and messages the chat from the job thread when it finishes.
- The workflow's wake-then-poll pattern exists because the free Render instance sleeps after ~15 idle minutes.

## Serving & security

- FastAPI serves the **built React app** as static files from the same port (`STATIC_DIR`, mounted after the API routes) — one service, one URL, no CORS in production.
- When `APP_PASSWORD` is set, every data endpoint requires `Authorization: Bearer <password>` (the UI shows a lock screen and stores the password in `localStorage`). The cron path authenticates with `X-Cron-Secret` instead. Comparisons use `secrets.compare_digest`. `/api/health` stays open for Render's health checks.
- Docker image is multi-stage: `node:22-alpine` builds the frontend, `python:3.12-slim` + [uv](https://docs.astral.sh/uv/) runs the backend. (Building locally on Apple Silicon needs `--platform linux/amd64` — the `libsql` wheel is amd64-only.)

## Free-tier constraints that shaped the design

| Constraint | Consequence in the design |
|---|---|
| Render free instance sleeps after ~15 min | cron workflow wakes it and polls every 30s to keep it awake during runs |
| Render free disk is ephemeral | Turso for persistence; in-memory jobs are acceptable losses |
| Turso round trips are ~200ms from Render | pooled connection, once-per-process DDL, 60s read cache |
| Single instance, no queue | background jobs are daemon threads + an in-memory dict |
| LLM spend per search | two model calls per search, capped web-search/fetch uses, watches run sequentially once a day |
