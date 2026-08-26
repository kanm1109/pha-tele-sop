"""Thin async client for the Telegram Bot API (sendMessage only).

Callers are expected to pass in a shared aiohttp.ClientSession (session pooling)
rather than opening a new connection per request.
"""
import logging

import aiohttp

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
TELEGRAM_GET_ME_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class TelegramAPIError(Exception):
    """Raised when Telegram responds with ok: false (400, 401, 429, ...)."""

    def __init__(self, error_code: int, description: str):
        self.error_code = error_code
        self.description = description
        super().__init__(f"Telegram API error {error_code}: {description}")


async def send_telegram_message(
    session: aiohttp.ClientSession,
    text: str,
    chat_id: str = None,
    parse_mode: str = "HTML",
) -> dict:
    """Send a message to a Telegram chat using the given shared session.

    Raises:
        TelegramAPIError: Telegram accepted the HTTP request but rejected the message.
        asyncio.TimeoutError: the request exceeded REQUEST_TIMEOUT.
        aiohttp.ClientConnectorError: DNS resolution / connection failure.
        aiohttp.ClientError: any other network-level failure.
    """
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }

    async with session.post(TELEGRAM_API_URL, json=payload, timeout=REQUEST_TIMEOUT) as response:
        data = await response.json()
        if not data.get("ok"):
            raise TelegramAPIError(
                data.get("error_code", response.status),
                data.get("description", "Unknown Telegram API error"),
            )
        logger.info("Telegram message sent (message_id=%s)", data["result"].get("message_id"))
        return data["result"]


async def get_me(session: aiohttp.ClientSession) -> dict:
    """Return the bot identity for TELEGRAM_BOT_TOKEN, used as a token health check.

    Raises the same exceptions as send_telegram_message — most notably TelegramAPIError
    with error_code 401 when the token has been revoked/rotated by the bot's owner.
    """
    async with session.get(TELEGRAM_GET_ME_URL, timeout=REQUEST_TIMEOUT) as response:
        data = await response.json()
        if not data.get("ok"):
            raise TelegramAPIError(
                data.get("error_code", response.status),
                data.get("description", "Unknown Telegram API error"),
            )
        return data["result"]
