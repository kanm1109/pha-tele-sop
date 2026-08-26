"""Thin async client for the Telegram Bot API (sendMessage, getMe).

Callers are expected to pass in a shared aiohttp.ClientSession (session pooling)
rather than opening a new connection per request. The bot token is held as
mutable module state so it can be swapped at runtime via set_token() — see
bot.py's !thaytoken command — without restarting the process.
"""
import logging

import aiohttp

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

_token = TELEGRAM_BOT_TOKEN


def get_token() -> str:
    return _token


def set_token(new_token: str) -> None:
    global _token
    _token = new_token


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_token}/{method}"


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

    async with session.post(_api_url("sendMessage"), json=payload, timeout=REQUEST_TIMEOUT) as response:
        data = await response.json()
        if not data.get("ok"):
            raise TelegramAPIError(
                data.get("error_code", response.status),
                data.get("description", "Unknown Telegram API error"),
            )
        logger.info("Telegram message sent (message_id=%s)", data["result"].get("message_id"))
        return data["result"]


async def get_me(session: aiohttp.ClientSession) -> dict:
    """Return the bot identity for the current token, used as a token health check.

    Raises the same exceptions as send_telegram_message — most notably TelegramAPIError
    with error_code 401 when the token has been revoked/rotated by the bot's owner.
    """
    async with session.get(_api_url("getMe"), timeout=REQUEST_TIMEOUT) as response:
        data = await response.json()
        if not data.get("ok"):
            raise TelegramAPIError(
                data.get("error_code", response.status),
                data.get("description", "Unknown Telegram API error"),
            )
        return data["result"]
