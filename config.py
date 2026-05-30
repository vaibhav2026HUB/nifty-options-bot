"""
Central configuration — reads from .env file.
All strategy parameters live here. Change nothing else when tuning the bot.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── MODE ──────────────────────────────────────────────────────────────────────
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").strip().lower() == "true"

# ─── BROKER ────────────────────────────────────────────────────────────────────
# "upstox"  → fully automatic via Upstox API (requires credentials below)
# "manual"  → email alerts only, you place orders yourself
# "kite"    → automatic via Kite Connect (legacy, requires KITE_* vars)
BROKER = os.getenv("BROKER", "manual").strip().lower()

# ─── CAPITAL ───────────────────────────────────────────────────────────────────
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "32500"))

# ─── UPSTOX ────────────────────────────────────────────────────────────────────
UPSTOX_API_KEY      = os.getenv("UPSTOX_API_KEY",      "")
UPSTOX_API_SECRET   = os.getenv("UPSTOX_API_SECRET",   "")
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1")
UPSTOX_MOBILE       = os.getenv("UPSTOX_MOBILE",       "")
UPSTOX_PIN          = os.getenv("UPSTOX_PIN",          "")
UPSTOX_TOTP_SECRET  = os.getenv("UPSTOX_TOTP_SECRET",  "")

# ─── KITE CONNECT (legacy) ─────────────────────────────────────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_TOKEN_FILE = "kite_token.txt"

# ─── STRATEGY ──────────────────────────────────────────────────────────────────
LOT_SIZE     = 65    # Nifty lot size (units per lot) — confirmed Mar 2026
SPREAD_WIDTH = 100   # Points between buy strike and sell strike
# Max debit per unit is now per-VIX-tier — see strategy/signal.py Signal.max_premium

# ─── VIX THRESHOLDS ────────────────────────────────────────────────────────────
# Tiered rules live in strategy/signal.py. Only the absolute floor is here.
# VIX < 11  → always skip (premium too thin)
# VIX 11–16 → Mon/Wed/Thu | 16–18 → Mon/Thu | 18–20 → Mon only | >20 → skip
VIX_MIN = 11
VIX_MAX = 20

# ─── RISK ──────────────────────────────────────────────────────────────────────
MAX_LOSS_PER_TRADE   = 6500   # ₹6,500 max risk per trade (65 units × ₹100)
DAILY_LOSS_LIMIT     = 6500   # ₹6,500 daily loss → bot shuts down for the day
WEEKLY_CAPITAL_FLOOR = 22750  # Capital < ₹22,750 → stop trading entire week (70% of ₹32,500)

# ─── EXIT ──────────────────────────────────────────────────────────────────────
# PT and SL multipliers are now per-VIX-tier (see strategy/signal.py)
SPOT_MOVE_STOP_PCT = 0.005   # 0.5% adverse Nifty move → exit (all tiers)

# ─── TIMING (IST, 24-hr format) ────────────────────────────────────────────────
ENTRY_CHECK_TIME    = "09:20"
ENTRY_EXECUTE_TIME  = "09:30"
LAST_ENTRY_TIME     = "11:00"
EXIT_CHECK_INTERVAL = 60

# These three default to normal market hours but can be overridden via env vars
# when running on GitHub Actions (GH_ACTIONS=true) to fit within the 6-hour job limit.
FORCE_EXIT_TIME  = os.getenv("FORCE_EXIT_TIME",  "15:00")  # GH Actions: "14:45"
EOD_SUMMARY_TIME = os.getenv("EOD_SUMMARY_TIME", "15:15")  # GH Actions: "14:50"
SHUTDOWN_TIME    = os.getenv("SHUTDOWN_TIME",    "15:30")  # GH Actions: "14:55"

_fet = FORCE_EXIT_TIME.split(":")
FORCE_EXIT_HOUR   = int(_fet[0])
FORCE_EXIT_MINUTE = int(_fet[1])

# ─── TRADING MODE (legacy — use BROKER= instead) ───────────────────────────────
# MANUAL_TRADING=True  → same as BROKER=manual (kept for backward compatibility)
MANUAL_TRADING = os.getenv("MANUAL_TRADING", "True").strip().lower() == "true"

# ─── EMAIL ALERTS (Gmail SMTP) ─────────────────────────────────────────────────
ALERT_EMAIL_FROM         = os.getenv("ALERT_EMAIL_FROM", "")
ALERT_EMAIL_APP_PASSWORD = os.getenv("ALERT_EMAIL_APP_PASSWORD", "")
ALERT_EMAIL_TO           = os.getenv("ALERT_EMAIL_TO", "")

# ─── TELEGRAM (optional, legacy) ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── PATHS ─────────────────────────────────────────────────────────────────────
TRADE_LOG_PATH = "logs/trades.csv"
STATE_FILE_PATH = "logs/state.json"
