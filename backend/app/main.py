"""FastAPI app: agent searches and watch runs as background jobs, deals + watches API.

Long work (searches ~1-2 min each, watch runs potentially several searches) never
blocks a request: POST endpoints return a job id immediately and clients poll
GET /api/jobs/{id}. Jobs run in daemon threads, live in memory and are mirrored
to the DB (app/jobs.py), so a restart marks in-flight ones "interrupted" instead
of losing them.

In production the built frontend (static files) is served from STATIC_DIR.
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import alerts, details, engine, explore, jobs, notify, storage, telegram_bot, trips, watcher
from .jobs import Job
from .models import (
    AlertRecord, RouteHistory, SearchRequest, StoredDeal, UsageReport, Watch, WatchCreate, WatchUpdate,
    search_label,
)
from .models import (
    BookedFlight, Trip, TripCreate, TripDetail, TripItem, TripItemCreate, TripItemUpdate, TripUpdate,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Jobs a previous process left "running" can never finish: say so (one
    # small DB call, which also warms the Turso connection for the first request).
    jobs.recover_interrupted()
    yield
    jobs.shutdown()


app = FastAPI(title="Flight Scout", version="2.3.1", lifespan=_lifespan)


def require_auth(
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
) -> None:
    """Gate API access behind APP_PASSWORD when it is set.

    Accepted credentials: `Authorization: Bearer <APP_PASSWORD>` (the web app),
    or a valid X-Cron-Secret (so the scheduled workflow can poll job status).
    With APP_PASSWORD unset the API is open — convenient for local dev.
    """
    app_password = os.environ.get("APP_PASSWORD")
    if not app_password:
        return
    if authorization and authorization.startswith("Bearer "):
        if secrets.compare_digest(authorization.removeprefix("Bearer "), app_password):
            return
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret and x_cron_secret and secrets.compare_digest(x_cron_secret, cron_secret):
        return
    raise HTTPException(status_code=401, detail="Invalid or missing app password")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- background jobs (app/jobs.py) -------------------------------------------

_start_job = jobs.start_job
_emit_event = jobs.emit_event
_emit_or_cancel = jobs.emit_or_cancel

_UNKNOWN_JOB = "Unknown job id"


@app.get("/api/jobs/{job_id}", response_model=Job, dependencies=[Depends(require_auth)])
def get_job(job_id: str) -> Job:
    """A job's status and result — from memory while this process runs it, else
    its stored record (so it survives restarts; see app/jobs.py)."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_JOB)
    return job


@app.post("/api/jobs/{job_id}/cancel", response_model=Job, dependencies=[Depends(require_auth)])
def cancel_job(job_id: str) -> Job:
    """Request a running job to stop. Cooperative: the job aborts at its next
    progress emission and flips to status "cancelled" without saving anything."""
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_JOB)
    return job


@app.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_auth)])
def stream_job_events(job_id: str, after: int = 0) -> StreamingResponse:
    """Server-Sent Events: replay this job's progress so far (from `after`),
    then follow it live until the job leaves the running state. The UI renders
    these as a streaming 'agent console'; polling /api/jobs/{id} remains the
    source of truth for completion, so a dropped stream degrades gracefully.
    A job from before a restart is replayed from its stored events."""
    events = jobs.event_stream(job_id, after)
    if events is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_JOB)

    def gen():
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- searches ---------------------------------------------------------------

@app.post("/api/search", response_model=Job, status_code=202, dependencies=[Depends(require_auth)])
def start_search(req: SearchRequest) -> Job:
    params = req.params()
    query = search_label(req.origin, req.destination, req.dates_hint,
                         req.direct_only, req.cabin_bag, req.checked_bag, params=params)

    def work(job_id: str) -> dict:
        outcome = engine.search(
            req.origin, req.destination, req.dates_hint, params,
            on_event=lambda event: _emit_or_cancel(job_id, event),
        )
        return {"deals": storage.save_deals(query, outcome.deals, params), "meta": outcome.meta}

    return _start_job("search", query, work, request=req.model_dump(mode="json"))


@app.get("/api/deals", response_model=list[StoredDeal], dependencies=[Depends(require_auth)])
def get_deals(
    origin: str | None = None,
    destination: str | None = None,
    max_price: float | None = None,
    limit: int = 200,
    dedupe: bool = True,
) -> list[StoredDeal]:
    """List deals. With dedupe (default), a deal re-found by later runs appears
    once — its latest sighting — carrying times_seen / first_found_at."""
    return storage.list_deals(origin, destination, max_price, limit, dedupe)


@app.get("/api/history", response_model=list[RouteHistory], dependencies=[Depends(require_auth)])
def get_history() -> list[RouteHistory]:
    """Best price per route per search run — the data behind the price chart."""
    return storage.get_route_history()


# --- watches ----------------------------------------------------------------

def _with_stats(watches: list[Watch]) -> list[Watch]:
    """Attach price stats (30-day low, typical, score...) and the last sent alert."""
    enriched = alerts.enrich_watches(watches, storage.watch_sightings(), storage.list_alerts(),
                                     insights=storage.watch_price_insights())
    return [w.model_copy(update={"credits": engine.watch_credits(w)}) for w in enriched]


@app.post("/api/watches", response_model=Watch, status_code=201, dependencies=[Depends(require_auth)])
def create_watch(watch: WatchCreate) -> Watch:
    return _with_stats([storage.add_watch(watch)])[0]


@app.get("/api/watches", response_model=list[Watch], dependencies=[Depends(require_auth)])
def get_watches() -> list[Watch]:
    """Every watch, paused ones included (active=false). The daily run and
    "Run now" only ever touch active watches. Each carries `stats` (null before
    its first comparable run) and `last_alert`."""
    return _with_stats(storage.list_watches())


@app.patch("/api/watches/{watch_id}", response_model=Watch, dependencies=[Depends(require_auth)])
def patch_watch(watch_id: int, patch: WatchUpdate) -> Watch:
    """Partial update: edit criteria/threshold, or pause/resume with {"active": false|true}."""
    current = storage.get_watch(watch_id)
    if current is None:
        raise HTTPException(status_code=404, detail="No such watch")
    try:
        fields, active = patch.apply_to(current)
    except ValidationError as exc:
        # Same 422 shape as a bad request body, for errors only visible once merged.
        raise RequestValidationError(exc.errors(include_url=False)) from exc
    updated = storage.update_watch(watch_id, fields, active)
    if updated is None:  # deleted in between
        raise HTTPException(status_code=404, detail="No such watch")
    return _with_stats([updated])[0]


@app.delete("/api/watches/{watch_id}", status_code=204, dependencies=[Depends(require_auth)])
def remove_watch(watch_id: int) -> None:
    if not storage.delete_watch(watch_id):
        raise HTTPException(status_code=404, detail="No such watch")


@app.post("/api/watches/{watch_id}/run", response_model=Job, status_code=202,
          dependencies=[Depends(require_auth)])
def run_single_watch(watch_id: int) -> Job:
    """Run one watch immediately (the UI's "Run now" button) as a background
    job with live progress events — same alert logic as the daily sweep."""
    watch = next((w for w in storage.list_watches(active_only=True) if w.id == watch_id), None)
    if watch is None:
        raise HTTPException(status_code=404, detail="No such active watch")
    query = watcher.watch_label(watch)

    def work(job_id: str) -> dict:
        summary = watcher.run_watches(
            only_id=watch_id, on_event=lambda event: _emit_or_cancel(job_id, event)
        )
        return {"summary": summary}

    return _start_job("watch_run", query, work, request={"watch_id": watch_id})


@app.post("/api/watches/run", response_model=Job, status_code=202)
def run_watches(x_cron_secret: str | None = Header(default=None)) -> Job:
    """Re-search all active watches and alert on threshold hits.

    Called by the scheduled GitHub Actions workflow. Protected by CRON_SECRET
    when set (always set it in production — each run spends LLM credit).
    """
    expected = os.environ.get("CRON_SECRET")
    if expected and x_cron_secret != expected:
        raise HTTPException(status_code=401, detail="Bad or missing X-Cron-Secret header")
    return _start_job("watch_run", "scheduled watch run", lambda _job_id: {"summary": watcher.run_watches()},
                      request={"scheduled": True})


@app.get("/api/alerts", response_model=list[AlertRecord], dependencies=[Depends(require_auth)])
def get_alerts(watch_id: int | None = None, limit: int = 50) -> list[AlertRecord]:
    """Sent alerts, newest first (the anti-spam log)."""
    return storage.list_alerts(watch_id)[:limit]


# --- trips (3.0; see app/trips.py) ---------------------------------------------

def _trip_or_404(trip_id: int) -> Trip:
    trip = storage.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="No such trip")
    return trip


@app.get("/api/trips", response_model=list[Trip], dependencies=[Depends(require_auth)])
def get_trips() -> list[Trip]:
    """Every trip, most recently changed first, each with its item count and
    best price with bags across its searches, watches and shortlist."""
    return trips.with_best(storage.list_trips())


@app.post("/api/trips", response_model=Trip, status_code=201, dependencies=[Depends(require_auth)])
def create_trip(trip: TripCreate) -> Trip:
    return storage.add_trip(trip)


@app.get("/api/trips/{trip_id}", response_model=TripDetail, dependencies=[Depends(require_auth)])
def get_trip(trip_id: int) -> TripDetail:
    """The trip with its links resolved: searches (latest run's cheapest deals),
    watches (with stats), shortlisted deals and the booked flight."""
    return trips.detail(_trip_or_404(trip_id))


@app.patch("/api/trips/{trip_id}", response_model=Trip, dependencies=[Depends(require_auth)])
def patch_trip(trip_id: int, patch: TripUpdate) -> Trip:
    current = _trip_or_404(trip_id)
    merged = current.model_dump(include=set(TripCreate.model_fields))
    merged.update(patch.model_dump(exclude_unset=True))
    try:
        fields = TripCreate.model_validate(merged)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc
    updated = storage.update_trip(trip_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="No such trip")
    return trips.with_best([updated])[0]


@app.delete("/api/trips/{trip_id}", status_code=204, dependencies=[Depends(require_auth)])
def remove_trip(trip_id: int) -> None:
    """Delete the trip and its links only — never the deals or watches."""
    if not storage.delete_trip(trip_id):
        raise HTTPException(status_code=404, detail="No such trip")


@app.post("/api/trips/{trip_id}/items", response_model=TripItem, status_code=201,
          dependencies=[Depends(require_auth)])
def add_trip_item(trip_id: int, item: TripItemCreate) -> TripItem:
    """Link a search, watch or deal; adding the same one twice returns the existing link."""
    _trip_or_404(trip_id)
    if item.kind == "deal" and storage.get_deal(item.ref_id) is None:
        raise HTTPException(status_code=404, detail="No such deal")
    if item.kind == "watch" and storage.get_watch(item.ref_id) is None:
        raise HTTPException(status_code=404, detail="No such watch")
    return storage.add_trip_item(trip_id, item)


@app.patch("/api/trips/{trip_id}/items/{item_id}", response_model=TripItem, dependencies=[Depends(require_auth)])
def patch_trip_item(trip_id: int, item_id: int, patch: TripItemUpdate) -> TripItem:
    updated = storage.update_trip_item(trip_id, item_id, note=patch.note, pinned=patch.pinned)
    if updated is None:
        raise HTTPException(status_code=404, detail="No such trip item")
    return updated


@app.delete("/api/trips/{trip_id}/items/{item_id}", status_code=204, dependencies=[Depends(require_auth)])
def remove_trip_item(trip_id: int, item_id: int) -> None:
    """Unlink an item; the search, watch or deal itself stays."""
    if not storage.delete_trip_item(trip_id, item_id):
        raise HTTPException(status_code=404, detail="No such trip item")


@app.put("/api/trips/{trip_id}/booked", response_model=Trip, dependencies=[Depends(require_auth)])
def mark_trip_booked(trip_id: int, booked: BookedFlight) -> Trip:
    """Record the booked flight and mark the trip booked (a "done" trip stays
    done). With a deal_id, blank fields are filled from that deal — the price
    with its bags, as the family pays it."""
    trip = _trip_or_404(trip_id)
    if booked.deal_id is not None:
        deal = storage.get_deal(booked.deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="No such deal")
        fill = {
            "airline": booked.airline or deal.airline,
            "flight_numbers": booked.flight_numbers or deal.flight_numbers,
            "depart_date": booked.depart_date or deal.depart_date,
            "return_date": booked.return_date or deal.return_date,
        }
        if booked.price is None:
            fill["price"] = deal.bags.total if deal.bags else deal.price
            fill["currency"] = deal.bags.currency if deal.bags else deal.currency
        booked = booked.model_copy(update=fill)
    if booked.booked_at is None:
        booked = booked.model_copy(update={"booked_at": datetime.now(timezone.utc)})
    status = "done" if trip.status == "done" else "booked"
    return trips.with_best([storage.set_trip_booked(trip_id, booked, status)])[0]


@app.delete("/api/trips/{trip_id}/booked", response_model=Trip, dependencies=[Depends(require_auth)])
def clear_trip_booked(trip_id: int) -> Trip:
    """Forget the booked flight; a booked trip goes back to planning."""
    trip = _trip_or_404(trip_id)
    status = "planning" if trip.status == "booked" else trip.status
    return trips.with_best([storage.set_trip_booked(trip_id, None, status)])[0]


# --- Telegram bot (webhook; see app/telegram_bot.py) -------------------------

def _bot_run(watch_id: int, chat_id: str) -> None:
    """/run: check one watch in a job, then message the chat with the result."""
    watch = storage.get_watch(watch_id)
    query = watcher.watch_label(watch) if watch else f"watch #{watch_id}"

    def work(job_id: str) -> dict:
        try:
            summary = watcher.run_watches(only_id=watch_id, notify=False,
                                          on_event=lambda event: _emit_event(job_id, event))
            text = telegram_bot.format_run_result(watch_id, summary)
        except Exception as exc:
            text = f"⚠️ Check of watch #{watch_id} failed: {exc}"[:500]
            summary = None
        try:
            notify.send_telegram(chat_id, text)
        except Exception:
            pass  # the run itself succeeded; the reply is best-effort
        if summary is None:
            raise RuntimeError(text)
        return {"summary": summary}

    _start_job("watch_run", query, work)


@app.post("/api/telegram/webhook/{secret}", include_in_schema=False)
def telegram_webhook(
    secret: str,
    update: dict = Body(...),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """Telegram delivers bot messages here. Both the path segment and Telegram's
    secret-token header must equal TELEGRAM_WEBHOOK_SECRET; messages from chats
    outside TELEGRAM_CHAT_ID are acknowledged (so Telegram doesn't retry) but
    ignored. The reply, if any, is returned as a Bot API call in the body."""
    expected = telegram_bot.webhook_secret()
    if not expected or not secrets.compare_digest(secret.encode(), expected.encode()):
        raise HTTPException(status_code=404, detail="Not Found")
    if not x_telegram_bot_api_secret_token or not secrets.compare_digest(
            x_telegram_bot_api_secret_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Bad secret token")
    try:
        return telegram_bot.handle_update(update, run_watch=_bot_run) or {}
    except Exception:
        # A 500 makes Telegram re-deliver the same update over and over.
        return {}


class TelegramSetup(BaseModel):
    base_url: str | None = None


@app.post("/api/telegram/setup", dependencies=[Depends(require_auth)])
def telegram_setup(request: Request, body: TelegramSetup | None = None) -> dict:
    """Register this deployment as the bot's webhook (Telegram setWebhook) and
    publish the command menu. Base URL: body.base_url, else PUBLIC_URL, else this
    request's own host over https."""
    base = (body.base_url if body else None) or os.environ.get("PUBLIC_URL") or \
        f"https://{request.headers.get('host', request.url.netloc)}"
    try:
        return telegram_bot.register_webhook(base)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/usage", response_model=UsageReport, dependencies=[Depends(require_auth)])
def get_usage() -> UsageReport:
    """Fare-API calls this calendar month (UTC) per provider, with the
    SerpApi budget left, plus the estimated AI (LLM) spend (`ai`) — feeds the
    UI's usage meter. No network."""
    return engine.usage_report()


# --- explore: open destinations on a map (app/explore.py) ---------------------

@app.post("/api/explore/quote", response_model=explore.ExploreQuote, dependencies=[Depends(require_auth)])
def explore_quote(req: explore.ExploreRequest) -> explore.ExploreQuote:
    """What POST /api/explore would cost now: 0 when cached, else 1 search. No network."""
    try:
        return explore.quote(req)
    except explore.ExploreError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@app.post("/api/explore", response_model=explore.ExploreResult, dependencies=[Depends(require_auth)])
def run_explore(req: explore.ExploreRequest) -> explore.ExploreResult:
    """The cheapest destinations from an origin (Google Travel Explore): one
    live search, free for 6h after. 409 = budget used up, 503 = not configured."""
    try:
        return explore.explore(req)
    except explore.ExploreError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


# --- deal details on demand (app/details.py) ----------------------------------

def _deal_or_404(deal_id: int) -> StoredDeal:
    deal = storage.get_deal(deal_id)
    if deal is None:
        raise HTTPException(status_code=404, detail="No such deal")
    return deal


@app.get("/api/deals/{deal_id}/details", response_model=details.DealDetails,
         dependencies=[Depends(require_auth)])
def get_deal_details(deal_id: int) -> details.DealDetails:
    """Cached return flight + sellers, or status "needs_fetch" with the live
    searches a fetch would cost. Never spends a search."""
    return details.get_details(_deal_or_404(deal_id))


@app.post("/api/deals/{deal_id}/details", response_model=details.DealDetails,
          dependencies=[Depends(require_auth)])
def fetch_deal_details(deal_id: int) -> details.DealDetails:
    """Spend up to 2 live searches (return leg + booking options), within the
    monthly budget; cached for 6h. 409 = budget too low, 503 = not configured."""
    try:
        return details.fetch_details(_deal_or_404(deal_id))
    except details.DetailsError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None


@app.get("/api/deals/{deal_id}/book/{option}", include_in_schema=False)
def book_deal(deal_id: int, option: int, part: str = "together", exp: int | None = None,
              sig: str | None = None, authorization: str | None = Header(default=None)) -> HTMLResponse:
    """A page that POSTs Google's booking request for one seller (Google then
    redirects to the seller). Opened as a plain link, so it is authorised by
    the signature details put on it, or the usual Bearer header."""
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
    if not details.book_link_valid(deal_id, option, part, exp, sig):
        try:
            require_auth(authorization, None)
        except HTTPException:
            return HTMLResponse(details.message_page(
                "Link expired", "Open the deal's details again in Flight Scout and pick the seller."),
                status_code=401, headers=headers)
    deal = storage.get_deal(deal_id)
    target = details.booking_request(deal, option, part) if deal else None
    if target is None:
        return HTMLResponse(details.message_page(
            "Booking link expired", "Refresh the deal's details in Flight Scout to get current sellers."),
            status_code=410, headers=headers)
    seller, url, post_data = target
    if not details.safe_target(url):
        return HTMLResponse(details.message_page("Can't open this link", "Google returned an unexpected address."),
                            status_code=502, headers=headers)
    nonce = details.new_nonce()
    # No form-action directive: browsers apply it to the redirects after the
    # POST too, which would block the hop from Google to the seller.
    headers["Content-Security-Policy"] = (f"default-src 'none'; script-src 'nonce-{nonce}'; "
                                          "style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
    return HTMLResponse(details.redirect_page(seller, url, post_data, nonce), headers=headers)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Serve the built frontend in production (must be mounted after the API routes).
_static_dir = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
