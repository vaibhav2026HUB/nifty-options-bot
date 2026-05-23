"""
Thin wrapper — routes all send_alert() calls to notifier.py (ntfy.sh).
Kept so existing imports don't break.
"""
from alerts.notifier import send_alert  # noqa: F401
