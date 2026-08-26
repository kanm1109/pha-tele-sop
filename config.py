"""Application configuration loaded from environment variables (.env)."""
import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Optional: Discord channel to post an alert to if the Telegram token stops working.
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID")

_missing = [
    name
    for name, value in (
        ("DISCORD_BOT_TOKEN", DISCORD_BOT_TOKEN),
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
    )
    if not value
]
if _missing:
    raise RuntimeError(
        "Missing required environment variable(s): "
        + ", ".join(_missing)
        + ". Copy .env.example to .env and fill in the values."
    )
