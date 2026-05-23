"""
Kite Connect authentication with daily token persistence.

Daily flow (manual — Option B):
  1. Before 9:20 AM, run:  python auth.py
  2. A URL is printed. Open it in your browser and log in to Zerodha.
  3. After login, Zerodha redirects to http://127.0.0.1:5000/?request_token=XXXXX
  4. Copy everything after "request_token=" from the URL bar.
  5. Paste it when prompted. The bot saves the access_token to kite_token.txt.
  6. Token is valid for the rest of that trading day — no need to repeat.

In PAPER_TRADING mode, this module is not used.
"""

import os
import logging
from datetime import date

logger = logging.getLogger(__name__)


def get_kite_client():
    """Return an authenticated KiteConnect instance, or None in paper mode."""
    from config import KITE_API_KEY, KITE_API_SECRET, KITE_TOKEN_FILE, PAPER_TRADING

    if PAPER_TRADING:
        logger.info("Paper trading mode — Kite client not needed.")
        return None

    if not KITE_API_KEY or not KITE_API_SECRET:
        raise ValueError(
            "KITE_API_KEY and KITE_API_SECRET must be set in .env for live trading.\n"
            "Get them from https://kite.trade → Create app → Personal."
        )

    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise ImportError("Run:  pip install kiteconnect")

    kite = KiteConnect(api_key=KITE_API_KEY)

    # Try loading a saved token from today
    token = _load_token(KITE_TOKEN_FILE)
    if token:
        kite.set_access_token(token)
        logger.info("Loaded today's access token from kite_token.txt.")
        return kite

    # No valid token found — guide the user through manual login
    login_url = kite.login_url()
    print("\n" + "=" * 60)
    print("  KITE LOGIN REQUIRED")
    print("=" * 60)
    print(f"\n  1. Open this URL in your browser:\n\n     {login_url}\n")
    print("  2. Log in to Zerodha.")
    print("  3. After the redirect, look at the URL bar.")
    print("     Copy the value after 'request_token='")
    print("=" * 60)

    request_token = input("\n  Paste request_token here: ").strip()
    session_data  = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
    access_token  = session_data["access_token"]

    _save_token(KITE_TOKEN_FILE, access_token)
    kite.set_access_token(access_token)
    logger.info("New access token saved to kite_token.txt.")
    return kite


def _load_token(filepath: str):
    """Return today's saved access token, or None if stale/missing."""
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r") as f:
        lines = f.read().strip().splitlines()
    if len(lines) < 2:
        return None
    saved_date, token = lines[0], lines[1]
    if saved_date == str(date.today()):
        return token
    logger.info("kite_token.txt is from a previous day — fresh login needed.")
    return None


def _save_token(filepath: str, token: str):
    with open(filepath, "w") as f:
        f.write(f"{date.today()}\n{token}")


# ── Run directly to authenticate before market open ───────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        kite = get_kite_client()
        if kite:
            profile = kite.profile()
            print(f"\n  Logged in as: {profile['user_name']} ({profile['user_id']})")
            print("  Token saved. You can now start the bot.\n")
    except Exception as e:
        print(f"\n  ERROR: {e}\n")
