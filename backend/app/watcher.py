"""Re-run watched routes and alert on the watch's alert rules (app/alerts.py).

Triggered by the scheduled GitHub Actions workflow via POST /api/watches/run
(one search per active watch, sequentially — searches are slow and parallelism
would just multiply API spend). With fare-API keys each watch is pre-screened
against the free Aviasales cache and spends a live Google Flights search only
when it may matter (engine.find_for_watch). The scheduled sweep sends ONE
digest message covering every watch; a single-watch "Run now" alerts
immediately, and only when a rule fires.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import agent, alerts, engine, storage
from .alerts import WatchResult
from .currency import convert
from .models import AlertRecord, BookingLink, SearchMeta, SearchParams, StoredDeal, Watch, search_label
from .notify import send_notification

logger = logging.getLogger(__name__)


def watch_params(watch: Watch) -> SearchParams:
    """The criteria a watch searches with. Its max_price is the ALERT threshold,
    not a search filter — filtering on it would hide the best-price-so-far the
    UI shows when nothing is under the threshold yet."""
    params = SearchParams(**watch.model_dump(include=set(SearchParams.model_fields)))
    return params.model_copy(update={"max_price": None})


def watch_label(watch: Watch) -> str:
    return search_label(watch.origin, watch.destination, watch.dates_hint,
                        watch.direct_only, watch.cabin_bag, watch.checked_bag,
                        params=watch_params(watch)) + f" [watch #{watch.id}]"


def _best_booking_link(deal: StoredDeal) -> BookingLink | None:
    """The booking link that pre-fills the most criteria (an airline link wins ties)."""
    def score(link: BookingLink) -> tuple:
        return (len(link.prefilled), link.kind == "airline")

    links = [link for link in deal.booking_links if link.url]
    return max(links, key=score) if links else None  # max keeps the first of equal scores


def best_link(deal: StoredDeal) -> str:
    """Most useful URL for an alert: the best booking link, else the deal's own URLs."""
    link = _best_booking_link(deal)
    return link.url if link else deal.booking_url or deal.source_url


def best_link_labeled(deal: StoredDeal) -> tuple[str, str] | None:
    """(button label, url) for messages, e.g. ("Wizz Air", "https://...")."""
    link = _best_booking_link(deal)
    if link:
        return link.label, link.url
    url = deal.booking_url or deal.source_url
    return (deal.airline, url) if url else None


def cheapest(watch: Watch, deals: list[StoredDeal]) -> tuple[float | None, StoredDeal | None]:
    """Best deal in the watch's currency. Deals come back in whatever currency
    the source page used, so ₪1,009 must be compared as ~$336 against a $300
    threshold — a raw number comparison would drop it."""
    candidates = []
    for d in deals:
        in_watch_ccy = convert(d.price, d.currency, watch.currency)
        if in_watch_ccy is not None:
            candidates.append((in_watch_ccy, d))
        else:
            logger.warning("watch #%s: cannot convert %s -> %s, deal skipped",
                           watch.id, d.currency, watch.currency)
    if not candidates:
        return None, None
    price, deal = min(candidates, key=lambda t: t[0])
    return round(price, 2), deal


def check_watch(watch: Watch, stored: list[StoredDeal], prior_rows: list[dict],
                recent_alerts: list[AlertRecord], now: datetime,
                google: alerts.GoogleHistory | None = None) -> WatchResult:
    """Score one run: best price, stats including this run, rule hits, and
    whether those hits repeat an alert sent in the last week. `google` is the
    watch's latest Google price insights (alerts.latest_google). Pure."""
    kind = alerts.fare_kind(watch)
    prior = alerts.run_bests(prior_rows, watch.currency, kind)
    best_price, best = cheapest(watch, stored)
    result = WatchResult(watch=watch, best_price=best_price, best=best)
    if best is None:
        result.stats = alerts.price_stats(prior, watch.currency, now, google)
        return result
    # This run's bests per trust tier, like the stored rows will read back.
    current = alerts.run_bests(alerts.deal_rows(stored, now), watch.currency, kind) or \
        [alerts.RunBest(now, best_price, alerts.source_tier(best.source))]
    result.stats = alerts.price_stats(prior + current, watch.currency, now, google)
    result.link = best_link_labeled(best)
    result.hits = alerts.evaluate_rules(best_price, prior, watch.alerts, watch.max_price, watch.currency, now,
                                        google)
    if result.hits:
        fingerprint = alerts.fare_fingerprint(best)
        result.suppressed = alerts.suppressed_by(fingerprint, best_price, recent_alerts, now)
    return result


def _google(watch: Watch, meta: SearchMeta, now: datetime) -> alerts.GoogleHistory | None:
    """Keep this run's Google price insights (live runs only) and return the
    watch's latest ones. Storage trouble only costs the seed, never the run."""
    kind = alerts.insight_kind(watch)
    try:
        if meta.price_insights is not None:
            storage.save_price_insights(watch.id, kind, meta.price_insights, now)
            return alerts.google_history(meta.price_insights, watch.currency, now, now)
        return alerts.latest_google(storage.watch_price_insights().get(watch.id, []), watch, watch.currency, now)
    except Exception:
        logger.exception("watch #%s: price insights unavailable", watch.id)
        return None


def _log_alert(r: WatchResult, now: datetime) -> None:
    storage.log_alert(AlertRecord(
        watch_id=r.watch.id,
        fingerprint=alerts.fare_fingerprint(r.best),
        price=r.best_price,
        currency=r.watch.currency,
        rule=",".join(h.rule for h in r.hits),
        score=r.stats.score if r.stats else None,
        sent_at=now,
    ))


def _summary_row(r: WatchResult, deals_found: int, alerted: bool) -> dict:
    w = r.watch
    return {
        "watch_id": w.id,
        "route": f"{w.origin} → {w.destination}",
        "deals_found": deals_found,
        "best_price": r.best_price,
        "best_price_original": f"{r.best.price} {r.best.currency}" if r.best else None,
        "currency": w.currency,
        "threshold": w.max_price,
        "rules": [h.rule for h in r.hits],
        "reasons": [h.text for h in r.hits],
        "suppressed": r.suppressed is not None,
        "score": r.stats.score if r.stats else None,
        "stats": r.stats.model_dump(mode="json") if r.stats else None,
        "alerted": alerted,
    }


def run_watches(only_id: int | None = None, on_event=None, notify: bool = True) -> dict:
    """Re-search watched routes. `only_id` restricts to a single watch (the UI's
    "Run now" button) and alerts immediately on a rule hit; without it this is
    the daily sweep, which sends one digest at the end. `notify=False` sends
    nothing (the Telegram bot's /run replies with the result itself).
    `on_event` streams live progress (see agent.OnEvent)."""
    watches = storage.list_watches(active_only=True)
    if only_id is not None:
        watches = [w for w in watches if w.id == only_id]
    digest = only_id is None
    rows: list[dict] = []
    checked: list[WatchResult] = []
    alerts_sent = 0

    def emit(text: str) -> None:
        if on_event:
            on_event({"kind": "phase", "text": text})

    for watch in watches:
        route = f"{watch.origin} → {watch.destination}"
        try:
            emit(f"Checking {route} ({watch.dates_hint}) — alert at ≤ {watch.max_price:,.0f} {watch.currency}")
            prior_rows = storage.watch_sightings().get(watch.id, [])  # read before this run is saved
            params = watch_params(watch)
            # Free cached pre-screen first; a paid live search only when it may matter.
            outcome = engine.find_for_watch(watch, params, prior_rows, on_event=on_event)
            deals = outcome.deals
            stored = storage.save_deals(watch_label(watch), deals, params)
            now = datetime.now(timezone.utc)
            recent = storage.list_alerts(watch.id, since=now - timedelta(days=alerts.REALERT_DAYS))
            r = check_watch(watch, stored, prior_rows, recent, now, _google(watch, outcome.meta, now))

            alerted = False
            if r.alerting and notify and not digest:
                title, plain, rich = alerts.format_instant(r)
                channels = send_notification(title, plain, r.link[1] if r.link else "", html=rich)
                alerted = bool(channels)
                if alerted:
                    _log_alert(r, now)
                    alerts_sent += 1
                logger.info("watch #%s alerted via %s", watch.id, channels)

            if r.best is None:
                emit(f"{route}: no comparable deals found this run.")
            elif r.hits:
                why = "; ".join(h.text for h in r.hits)
                if r.suppressed is not None:
                    emit(f"{route}: best {r.best_price:,.0f} {watch.currency} — {why}. Already alerted for this fare, no repeat.")
                elif digest:
                    emit(f"{route}: best {r.best_price:,.0f} {watch.currency} — {why}. In today's digest.")
                else:
                    emit(f"{route}: best {r.best_price:,.0f} {watch.currency} — {why}."
                         + (" Alert sent!" if alerted else ""))
            else:
                emit(f"{route}: best {r.best_price:,.0f} {watch.currency} vs threshold {watch.max_price:,.0f} — no alert.")

            # last_best_price is displayed in the watch's currency by the UI.
            storage.update_watch_run(watch.id, r.best_price)
            checked.append(r)
            rows.append({**_summary_row(r, len(stored), alerted),
                         "sources": outcome.meta.sources, "prescreen": outcome.meta.decision})
        except agent.SearchCancelled:
            raise  # user aborted a manual run — stop, don't continue to other watches
        except Exception as exc:
            logger.exception("watch #%s failed", watch.id)
            checked.append(WatchResult(watch=watch, error=str(exc)))
            rows.append({"watch_id": watch.id, "route": route, "error": str(exc)})

    digest_channels: list[str] = []
    if digest and notify and checked:
        now = datetime.now(timezone.utc)
        hits = [r for r in checked if r.alerting]
        title, plain, rich = alerts.format_digest(checked, now)
        top = next((r.link[1] for r in hits if r.link), "")
        try:
            digest_channels = send_notification(title, plain, top, html=rich, quiet=not hits)
        except Exception:
            logger.exception("digest delivery failed")
        if digest_channels:
            for r in hits:
                _log_alert(r, now)
            alerts_sent = len(hits)
            for row in rows:
                if row.get("rules") and not row.get("suppressed"):
                    row["alerted"] = True

    summary = {"watches_run": len(watches), "alerts_sent": alerts_sent, "results": rows}
    if digest:
        summary["digest_channels"] = digest_channels
    return summary
