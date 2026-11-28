"""Multi-channel notification dispatchers (Discord REST, Telegram, Webhook)."""

import logging
from typing import Any, Optional, Tuple

import aiohttp

from database import utc_now_iso

logger = logging.getLogger(__name__)

_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
}


def format_price(price: Optional[float], currency: str = "USD") -> str:
    """Format a numeric price with its currency symbol or code."""
    if price is None:
        return "N/A"
    code = (currency or "USD").strip().upper() or "USD"
    symbol = _CURRENCY_SYMBOLS.get(code)
    if symbol:
        return f"{symbol}{price:,.2f}"
    return f"{code} {price:,.2f}"


def build_webhook_payload(
    *,
    event: str,
    product: str,
    url: str,
    status: str,
    price: Optional[float],
    message: str,
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Build a structured JSON payload for generic webhook notifications."""
    return {
        "event": event,
        "product": product,
        "url": url,
        "status": status,
        "price": price,
        "message": message,
        "timestamp": timestamp or utc_now_iso(),
    }


async def send_discord_rest_message(
    token: str,
    channel_id: str,
    message: str,
    embed: Optional[dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """Send a message (and optional embed) to a Discord channel via REST API."""
    clean_token = token.strip()
    clean_channel = str(channel_id).strip()
    if not clean_token or not clean_channel:
        return False, "Discord token and channel ID are required."

    url = f"https://discord.com/api/v10/channels/{clean_channel}/messages"
    headers = {
        "Authorization": f"Bot {clean_token}",
        "Content-Type": "application/json",
    }
    body_json: dict[str, Any] = {"content": message}
    if embed is not None:
        body_json["embeds"] = [embed]

    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=body_json) as response:
                if response.status in (200, 201, 204):
                    return True, None
                body = await response.text()
                return (
                    False,
                    f"Discord API error (HTTP {response.status}): {body[:200]}",
                )
    except Exception as exc:
        return False, f"Failed to reach Discord API: {exc}"


async def send_telegram_notification(
    token: str,
    chat_id: str,
    message: str,
) -> Tuple[bool, Optional[str]]:
    """Send a notification message via the Telegram Bot API."""
    clean_token = token.strip()
    clean_chat_id = str(chat_id).strip()
    if not clean_token or not clean_chat_id:
        return False, "Telegram bot token and chat ID are required."

    url = f"https://api.telegram.org/bot{clean_token}/sendMessage"
    payload = {
        "chat_id": clean_chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                if response.status in (200, 201, 204):
                    return True, None
                body = await response.text()
                return (
                    False,
                    f"Telegram API error (HTTP {response.status}): {body[:200]}",
                )
    except Exception as exc:
        return False, f"Failed to reach Telegram API: {exc}"


async def send_webhook_notification(
    webhook_url: str,
    payload: dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Send a JSON notification payload to a generic HTTP/HTTPS webhook URL."""
    clean_url = webhook_url.strip()
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        return False, "Valid HTTP/HTTPS webhook URL is required."

    headers = {"Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                clean_url, headers=headers, json=payload
            ) as response:
                if 200 <= response.status < 300:
                    return True, None
                body = await response.text()
                return (
                    False,
                    f"Webhook error (HTTP {response.status}): {body[:200]}",
                )
    except Exception as exc:
        return False, f"Failed to reach webhook URL: {exc}"
