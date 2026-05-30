"""
Telegram alert sender via Bot API.
Sends messages to the configured chat — no extra packages needed (uses requests).
"""

import logging
import requests
import config

logger = logging.getLogger(__name__)

_ENABLED = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"


def send_telegram(message: str):
    if not _ENABLED:
        return
    try:
        requests.post(
            _API,
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram alert failed (non-critical): {e}")
