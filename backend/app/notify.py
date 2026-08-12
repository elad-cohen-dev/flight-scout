"""Push notifications for price alerts.

Channels are enabled by env vars; any combination works:
- NTFY_TOPIC                              → https://ntfy.sh/<topic> (zero-signup push;
                                            subscribe to the topic in the ntfy app/web)
- TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   → Telegram bot message
"""

from __future__ import annotations

import os

import httpx


def send_notification(title: str, message: str, click_url: str = "") -> list[str]:
    """Send to every configured channel. Returns the channels that succeeded."""
    sent: list[str] = []

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        headers = {"Title": title, "Tags": "airplane,moneybag"}  # header values must stay ASCII
        if click_url:
            headers["Click"] = click_url
        httpx.post(
            f"https://ntfy.sh/{topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=20,
        ).raise_for_status()
        sent.append("ntfy")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        text = f"✈️ {title}\n{message}" + (f"\n{click_url}" if click_url else "")
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=20,
        ).raise_for_status()
        sent.append("telegram")

    return sent
