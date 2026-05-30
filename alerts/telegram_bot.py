"""
Telegram alert sender + command listener via Bot API.
No extra packages needed (uses requests).

Commands the bot listens for:
  /status  — current position and P&L
  /exit    — force-exit all positions immediately
"""

import logging
import requests
import config

logger = logging.getLogger(__name__)

_ENABLED = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
_SEND    = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
_UPDATES = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"

_last_update_id = 0


def send_telegram(message: str):
    if not _ENABLED:
        return
    try:
        requests.post(
            _SEND,
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram send failed (non-critical): {e}")


def check_commands() -> list[str]:
    """
    Poll for new messages. Returns list of commands received (e.g. ['/status', '/exit']).
    Call this in the main loop every 60s. Safe to ignore the return value.
    """
    global _last_update_id
    if not _ENABLED:
        return []
    try:
        resp = requests.get(
            _UPDATES,
            params={"offset": _last_update_id + 1, "timeout": 2},
            timeout=10,
        )
        updates = resp.json().get("result", [])
        commands = []
        for u in updates:
            _last_update_id = u["update_id"]
            text = u.get("message", {}).get("text", "")
            if text.startswith("/"):
                commands.append(text.strip().lower())
        return commands
    except Exception as e:
        logger.warning(f"Telegram poll failed (non-critical): {e}")
        return []
