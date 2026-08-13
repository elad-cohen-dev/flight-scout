"""Re-run watched routes and alert when a deal is at or below the watch threshold.

Triggered by the scheduled GitHub Actions workflow via POST /api/watches/run
(one agent search per active watch, sequentially — searches are slow and
parallelism would just multiply API spend).
"""

from __future__ import annotations

import logging

from . import agent, storage
from .currency import convert
from .notify import send_notification

logger = logging.getLogger(__name__)


def run_watches() -> dict:
    watches = storage.list_watches(active_only=True)
    results: list[dict] = []
    alerts = 0

    for watch in watches:
        route = f"{watch.origin} → {watch.destination}"
        try:
            deals = agent.find_deals(
                watch.origin, watch.destination, watch.dates_hint,
                watch.direct_only, watch.currency,
            )
            constraint = ", direct only" if watch.direct_only else ""
            query = f"{watch.origin} -> {watch.destination} ({watch.dates_hint}{constraint}) [watch #{watch.id}]"
            stored = storage.save_deals(query, deals)

            # Deals come back in whatever currency the source page used, so the
            # threshold comparison happens in the watch's currency — ₪1,009 is
            # UNDER a $300 threshold, and a raw number comparison would drop it.
            candidates = []
            for d in stored:
                in_watch_ccy = convert(d.price, d.currency, watch.currency)
                if in_watch_ccy is not None:
                    candidates.append((in_watch_ccy, d))
                else:
                    logger.warning("watch #%s: cannot convert %s -> %s, deal skipped",
                                   watch.id, d.currency, watch.currency)
            best_price, best = min(candidates, key=lambda t: t[0]) if candidates else (None, None)

            alerted = False
            if best is not None and best_price <= watch.max_price:
                price_str = f"{best.price:,.0f} {best.currency}"
                if best.currency != watch.currency:
                    price_str += f" (~{best_price:,.0f} {watch.currency})"
                message = (
                    f"{route}: {price_str} on {best.airline}\n"
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

            # last_best_price is displayed in the watch's currency by the UI.
            storage.update_watch_run(watch.id, round(best_price, 2) if best else None)
            results.append({
                "watch_id": watch.id,
                "route": route,
                "deals_found": len(stored),
                "best_price": round(best_price, 2) if best else None,
                "best_price_original": f"{best.price} {best.currency}" if best else None,
                "currency": watch.currency,
                "threshold": watch.max_price,
                "alerted": alerted,
            })
        except Exception as exc:
            logger.exception("watch #%s failed", watch.id)
            results.append({"watch_id": watch.id, "route": route, "error": str(exc)})

    return {"watches_run": len(watches), "alerts_sent": alerts, "results": results}
