# Flight Scout — Architecture

End-to-end: a LangGraph agent that searches the live web for flight deals, a FastAPI backend that runs it as background jobs and serves the React UI, a Turso (libSQL) database, and a GitHub Actions cron that re-checks watched routes daily and pushes alerts.

## System overview

```mermaid
flowchart LR
    subgraph client["Browser"]
        UI["React + Vite SPA"]
    end

    subgraph render["Render (one Docker service, free tier)"]
        API["FastAPI<br/>app/main.py"]
        AGENT["LangGraph agent<br/>app/agent.py"]
        WATCHER["Watcher<br/>app/watcher.py"]
        STORE["Storage layer<br/>app/storage.py"]
        STATIC["Static files<br/>frontend/dist"]
    end

    subgraph external["External services"]
        LLM["LLM provider<br/>Anthropic / OpenAI<br/>+ server-side web search"]
        DB[("Turso<br/>hosted libSQL")]
        FX["frankfurter.dev<br/>ECB exchange rates"]
        NTFY["ntfy.sh"]
        TG["Telegram Bot API"]
    end

    CRON["GitHub Actions<br/>daily cron 05:00 UTC"]

    UI -->|"/api/*  (Bearer APP_PASSWORD)"| API
    API --> STATIC
    API -->|background thread| AGENT
    CRON -->|"POST /api/watches/run<br/>(X-Cron-Secret)"| API
    API --> WATCHER
    WATCHER --> AGENT
    AGENT -->|2 model calls per search| LLM
    WATCHER --> STORE
    API --> STORE
    STORE --> DB
    WATCHER -->|threshold check| FX
    WATCHER -->|alert| NTFY
    WATCHER -->|alert| TG
```

Everything server-side lives in **one process** on one free Render instance: the API, the agent, the watcher, and the static frontend. There is no queue, no worker fleet, no scraping infrastructure — the design goal is the smallest system that does the job on free tiers.

## The LangGraph agent (`backend/app/agent.py`)

The agent is a two-node `StateGraph`:

```mermaid
stateDiagram-v2
    [*] --> research
    research --> extract
    extract --> [*]

    research : research — LLM + server-side web search, returns prose findings
    extract : extract — structured output pass, returns validated FlightDeal list
```

State flowing through the graph (`AgentState`):

```python
origin: str            # "TLV"
destination: str       # "Prague"
dates_hint: str        # free text: "October 11 week", "a weekend in November"
direct_only: bool      # constrain to non-stop flights
currency: str          # preferred quote currency (the watch's currency; "USD" default)
research_notes: str    # output of node 1, input of node 2
deals: list[FlightDeal]  # final output
```

### Node 1 — `research`

One LLM call with the provider's **server-side web search tool** bound. The searching loop (issue queries, fetch pages, read results) runs entirely on the provider's infrastructure and the node gets back a single prose answer — no client-side tool loop, no scraper, no flight-API keys:

- **Anthropic**: `{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}`
- **OpenAI**: `{"type": "web_search"}` — requires the Responses API (`ChatOpenAI(use_responses_api=True)`)

The prompt asks for concrete deals (price + currency, airline, dates, duration, stops, booking/source URLs, caveats) from Google Flights result pages, airline deal pages, and fare aggregators. Two conditional clauses are injected from state: a hard "only DIRECT (non-stop) flights" instruction when `direct_only` is set, and a preferred quote currency.

### Node 2 — `extract`

A second, cheaper LLM pass over the research notes using LangChain's `with_structured_output(FlightDealList)` — the model is forced to emit the Pydantic schema, so the output is validated data, not parsed prose. Two safety nets sit on top of the model:

- **Direct-only is enforced in code**: when `direct_only` is set, deals with `stops != 0` are filtered out after extraction. The prompt asks; the filter guarantees.
- **Currency codes are normalized by a Pydantic validator** on `FlightDeal` (`"₪"` → `ILS`, `"$"` → `USD`, ...). The same model validates rows read back from the DB, so old mis-coded rows heal on read.

### Why two passes?

A single call can't reliably do both jobs: web search needs a free-form response the model can reason in, while storage needs strict schema. Splitting them also means the second pass can run on the same cheap model with no tools bound. A full search costs **two model calls** (~$0.01–0.03 on `gpt-5.4-mini`).

### Provider selection

`_provider()` picks the provider at call time: `FLIGHT_SCOUT_PROVIDER` env override → `ANTHROPIC_API_KEY` present → `OPENAI_API_KEY` present. Default models: `claude-opus-5` / `gpt-5.4-mini`, overridable with `FLIGHT_SCOUT_MODEL`.

## Request flow — what happens on "Find deals"

A live web search takes 1–2 minutes, far beyond sane HTTP timeouts (and Render's proxy limits), so searches run as **background jobs**:

```mermaid
sequenceDiagram
    participant UI as React app
    participant API as FastAPI
    participant T as job thread
    participant LLM as LLM + web search
    participant DB as Turso

    UI->>API: POST /api/search {origin, destination, dates_hint, direct_only}
    API->>T: spawn daemon thread
    API-->>UI: 202 {job_id, status: running}
    T->>LLM: research (server-side web search)
    LLM-->>T: prose findings
    T->>LLM: extract (structured output)
    LLM-->>T: FlightDeal list
    T->>DB: INSERT deals
    loop every 3s
        UI->>API: GET /api/jobs/{job_id}
        API-->>UI: running… / done + deals
    end
```

Jobs live in an in-memory dict guarded by a lock — fine for a single instance; a restart loses in-flight jobs but never stored data. The same mechanism runs watch sweeps (`kind: watch_run`).

## Storage (`backend/app/storage.py`)

Two interchangeable backends behind one module — both speak the sqlite3 DB-API:

| | Local dev | Production |
|---|---|---|
| Backend | SQLite file (`backend/flight_scout.db`) | [Turso](https://turso.tech) hosted libSQL |
| Selected by | `TURSO_DATABASE_URL` unset | `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` set |
| Why | zero setup | Render free tier has an **ephemeral disk** — a local file would vanish on every deploy/restart |

Two tables: `deals` (every deal from every run, append-only) and `watches`. Schema is created on first connect; lightweight migrations are `ALTER TABLE` statements that fail harmlessly once applied.

Performance shape (matters because every Turso statement is a network round trip):

- **One pooled connection per process**, serialized by a lock (writes happen on job threads while requests read). Schema DDL runs once per process, not per request. A dropped idle connection is detected and retried once on a fresh connection — note that libsql surfaces dead-connection errors as a `BaseException`-derived pyo3 panic, which the retry deliberately catches.
- **60-second in-process read cache** over deals/watches/history, invalidated wholesale by every write — so the UI sees fresh data the instant a search or watch run lands. Before this, a page load cost ~24 Turso round trips (~5s); warm loads now cost zero.

Read-time shaping (the DB stays raw; views are computed):

- **De-duplication**: identical deals re-found across runs (same route/airline/dates/price) collapse into one row carrying `times_seen` and `first_found_at`. A *changed* price is deliberately a new entry — repricing is information.
- **Price history**: deals group by the *requested* route parsed from the search query (extracted deals name airports LTN/LHR/London inconsistently), each run contributes its minimum price, and every price is **converted to the route's dominant currency** first — runs quote whatever currency the source pages used.

## Watches, cron, and notifications

```mermaid
sequenceDiagram
    participant GH as GitHub Actions (05:00 UTC)
    participant API as FastAPI on Render
    participant W as watcher
    participant FX as frankfurter.dev
    participant N as ntfy / Telegram

    GH->>API: GET /api/health (retry loop — wakes the sleeping instance)
    GH->>API: POST /api/watches/run (X-Cron-Secret)
    API-->>GH: 202 {job_id}
    loop each active watch
        W->>W: agent.find_deals(origin, dest, dates, direct_only, currency)
        W->>FX: convert each deal price to the watch's currency
        alt best converted price ≤ max_price
            W->>N: push alert ("1,009 ILS (~336 USD) on Israir…")
        end
    end
    loop every 30s (also keeps Render awake)
        GH->>API: GET /api/jobs/{job_id} (X-Cron-Secret)
    end
```

- The **threshold comparison is currency-aware** (`app/currency.py`): deal prices convert into the watch's currency using ECB rates from frankfurter.dev (keyless, cached in-process for 12h) with a static fallback table. Raw-number comparison was the original design and it failed both ways: ₪1,009 vs a $300 threshold never alerted; £95 vs a $100 threshold would have alerted wrongly.
- Alerts go to **every configured channel**: ntfy (topic-based, zero signup) and/or a Telegram bot.
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
| LLM spend per search | mini-tier model, two calls per search, watches run sequentially once a day |
