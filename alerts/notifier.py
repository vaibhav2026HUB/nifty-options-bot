"""
Email alert sender via Gmail SMTP.

Uses Python's built-in smtplib — no extra packages needed.

Setup (one-time, 5 minutes):
  1. Go to myaccount.google.com → Security → 2-Step Verification → enable it
  2. Then go to myaccount.google.com/apppasswords
  3. App name: "NiftyBot" → Generate → copy the 16-character password
  4. Add to .env:
       ALERT_EMAIL_FROM=yourgmail@gmail.com
       ALERT_EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
       ALERT_EMAIL_TO=yourphone@gmail.com
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import config

logger = logging.getLogger(__name__)

_ENABLED = bool(
    config.ALERT_EMAIL_FROM
    and config.ALERT_EMAIL_APP_PASSWORD
    and config.ALERT_EMAIL_TO
)


def send_alert(message: str, title: str = "Nifty Bot", priority: str = "default"):
    """Send alert via email + Telegram. Either channel failing won't block the other."""
    from alerts.telegram_bot import send_telegram
    send_telegram(f"<b>{title}</b>\n{message}")

    if not _ENABLED:
        logger.debug("Email not configured — skipping email alert.")
        return
    try:
        msg = MIMEMultipart()
        msg["From"]    = config.ALERT_EMAIL_FROM
        msg["To"]      = config.ALERT_EMAIL_TO
        msg["Subject"] = f"[NiftyBot] {title}"
        msg.attach(MIMEText(message, "plain"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.starttls()
            server.login(config.ALERT_EMAIL_FROM, config.ALERT_EMAIL_APP_PASSWORD)
            server.sendmail(config.ALERT_EMAIL_FROM, config.ALERT_EMAIL_TO, msg.as_string())

        logger.info(f"Email alert sent: {title}")
    except Exception as e:
        logger.warning(f"Email alert failed (non-critical): {e}")
