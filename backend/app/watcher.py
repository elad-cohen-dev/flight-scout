"""Re-run watched routes and alert when a deal is at or below the watch threshold.

Triggered by the scheduled GitHub Actions workflow via POST /api/watches/run
(one agent search per active watch, sequentially — searches are slow and
parallelism would just multiply API spend).
"""

from __future__ import annotations

import logging

from . import agent, storage
from .notify import send_notification

logger = logging.getLogger(__name__)


def run_watches() -> dict:
    watches = storage.list_watches(active_only=True)
    results: list[dict] = []
    alerts = 0

    for watch in watches:
        route = f"{watch.origin} → {watch.destination}"
        try:
            deals = agent.find_deals(watch.origin, watch.destination, watch.dates_hint)
            query = f"{watch.origin} -> {watch.destination} ({watch.dates_hint}) [watch #{watch.id}]"
            stored = storage.save_deals(query, deals)
            best = min(stored, key=lambda d: d.price) if stored else None

            alerted = False
            if best is not None and best.price <= watch.max_price:
                message = (
                    f"{route}: {best.price:,.0f} {best.currency} on {best.airline}\n"
                    f"{best.depart_date}{' — ' + best.return_date if best.return_date else ''}"
                    f"{', ' + best.duration if best.duration else ''}"
                    f" ({'direct' if best.stops == 0 else str(best.stops) + ' stop(s)'})\n"
                    f"Your threshold: {watch.max_price:,.0f} {watch.currency}"
                )
                channels = send_notification(
                    "Flight deal below your threshold!",
                    message,
                    best.booking_url or best.source_url,
                )
                alerted = bool(channels)
                if alerted:
                    alerts += 1
                logger.info("watch #%s alerted via %s", watch.id, channels)

            storage.update_watch_run(watch.id, best.price if best else None)
            results.append({
                "watch_id": watch.id,
                "route": route,
                "deals_found": len(stored),
                "best_price": best.price if best else None,
                "threshold": watch.max_price,
                "alerted": alerted,
            })
        except Exception as exc:
            logger.exception("watch #%s failed", watch.id)
            results.append({"watch_id": watch.id, "route": route, "error": str(exc)})

    return {"watches_run": len(watches), "alerts_sent": alerts, "results": results}
