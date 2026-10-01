"""Push notifications for price alerts.

Channels are enabled by env vars; any combination works:
- NTFY_TOPIC                              → https://ntfy.sh/<topic> (zero-signup push;
                                            subscribe to the topic in the ntfy app/web)
- TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   → Telegram bot message; TELEGRAM_CHAT_ID
                                            may be a comma-separated list of chat IDs
                                            (also the allow-list of the two-way bot,
                                            see app/telegram_bot.py)
"""

from __future__ import annotations

import base64
import os

import httpx


def chat_ids() -> list[str]:
    """TELEGRAM_CHAT_ID as a list: the alert recipients, and the only chats the bot answers."""
    return [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]


def telegram_api(method: str, payload: dict) -> dict:
    """Call a Bot API method (sendMessage, setWebhook, ...); raises on HTTP errors."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    res = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20)
    res.raise_for_status()
    return res.json()


def send_telegram(chat_id: str, html: str, quiet: bool = False) -> None:
    """One HTML-formatted message to one chat, without link previews (they'd
    bury a multi-watch digest under a booking-site card)."""
    telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": html,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        "disable_notification": quiet,
    })


def send_notification(title: str, message: str, click_url: str = "",
                      html: str | None = None, quiet: bool = False) -> list[str]:
    """Send to every configured channel. Returns the channels that succeeded.

    `message` is plain text (ntfy); `html`, when given, is the Telegram version
    (parse_mode HTML, links inline) — else Telegram gets title + message + URL.
    `quiet` sends at low priority / silently: a digest with nothing new."""
    sent: list[str] = []

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        headers = {"Title": title, "Tags": "airplane,moneybag"}  # header values must stay ASCII
        if not title.isascii():  # RFC 2047 encoded-word, which ntfy decodes
            headers["Title"] = "=?UTF-8?B?" + base64.b64encode(title.encode("utf-8")).decode("ascii") + "?="
        if quiet:
            headers["Priority"] = "low"
        if click_url and click_url.isascii():  # booking URLs are normally percent-encoded
            headers["Click"] = click_url
        httpx.post(
            f"https://ntfy.sh/{topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=20,
        ).raise_for_status()
        sent.append("ntfy")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    recipients = chat_ids()
    if token and recipients:
        if html is None:
            text = f"✈️ {title}\n{message}" + (f"\n{click_url}" if click_url else "")
        delivered = 0
        last_error: Exception | None = None
        for chat_id in recipients:
            # One blocked/invalid chat must not stop delivery to the others.
            try:
                if html is not None:
                    send_telegram(chat_id, html, quiet=quiet)
                else:
                    telegram_api("sendMessage", {"chat_id": chat_id, "text": text})
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if delivered:
            sent.append("telegram")
        elif last_error is not None:
            raise last_error

    return sent
