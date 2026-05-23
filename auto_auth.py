"""
Automated Kite Connect daily login — no manual steps.

Flow:
  1. POST credentials to Kite login endpoint
  2. Generate TOTP from stored secret (pyotp)
  3. POST TOTP to complete 2FA
  4. Extract request_token from redirect URL
  5. Generate session → access_token
  6. Save to kite_token.txt with today's date

Run via Task Scheduler at 8:30 AM Mon-Fri, before main bot starts at 9:00 AM.

Requires in .env:
  ZERODHA_USER_ID=AB1234
  ZERODHA_PASSWORD=yourpassword
  ZERODHA_TOTP_SECRET=BASE32SECRETFROMZERODHA
  KITE_API_KEY=your_api_key
  KITE_API_SECRET=your_api_secret
"""

import os
import sys
import time
import logging
from datetime import date
from urllib.parse import urlparse, parse_qs

import requests
import pyotp
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
USER_ID      = os.getenv("ZERODHA_USER_ID", "").strip()
PASSWORD     = os.getenv("ZERODHA_PASSWORD", "").strip()
TOTP_SECRET  = os.getenv("ZERODHA_TOTP_SECRET", "").strip()
API_KEY      = os.getenv("KITE_API_KEY", "").strip()
API_SECRET   = os.getenv("KITE_API_SECRET", "").strip()
TOKEN_FILE   = "kite_token.txt"

LOGIN_URL    = "https://kite.zerodha.com/api/login"
TWOFA_URL    = "https://kite.zerodha.com/api/twofa"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/auth.log"),
    ]
)
logger = logging.getLogger(__name__)


def _validate_env():
    missing = []
    for name, val in [
        ("ZERODHA_USER_ID",     USER_ID),
        ("ZERODHA_PASSWORD",    PASSWORD),
        ("ZERODHA_TOTP_SECRET", TOTP_SECRET),
        ("KITE_API_KEY",        API_KEY),
        ("KITE_API_SECRET",     API_SECRET),
    ]:
        if not val:
            missing.append(name)
    if missing:
        logger.error(f"Missing required env vars: {', '.join(missing)}")
        logger.error("Add them to your .env file — see .env.example")
        sys.exit(1)


def _login_step1(session: requests.Session) -> str:
    """POST credentials, returns request_id for 2FA."""
    logger.info("Step 1: Submitting credentials...")
    resp = session.post(LOGIN_URL, data={
        "user_id":  USER_ID,
        "password": PASSWORD,
    }, timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(f"Login failed (HTTP {resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Login rejected: {data.get('message', data)}")

    request_id = data["data"]["request_id"]
    logger.info(f"Step 1 OK — request_id: {request_id}")
    return request_id


def _login_step2(session: requests.Session, request_id: str) -> str:
    """Submit TOTP, follow redirect, extract request_token."""
    totp = pyotp.TOTP(TOTP_SECRET).now()
    logger.info(f"Step 2: Submitting TOTP ({totp})...")

    resp = session.post(TWOFA_URL, data={
        "user_id":    USER_ID,
        "request_id": request_id,
        "twofa_value": totp,
        "twofa_type": "totp",
    }, timeout=30, allow_redirects=True)

    # Zerodha redirects to the app's redirect URI after 2FA succeeds.
    # The final URL contains request_token as a query param.
    final_url = resp.url
    logger.info(f"Step 2 final URL: {final_url}")

    parsed = urlparse(final_url)
    params = parse_qs(parsed.query)

    if "request_token" in params:
        token = params["request_token"][0]
        logger.info(f"request_token extracted: {token[:10]}...")
        return token

    # Sometimes token is in JSON body instead of redirect
    try:
        body = resp.json()
        if body.get("status") == "success":
            token = body["data"].get("request_token")
            if token:
                logger.info(f"request_token from JSON: {token[:10]}...")
                return token
    except Exception:
        pass

    raise RuntimeError(
        f"2FA completed but could not find request_token.\n"
        f"Final URL: {final_url}\n"
        f"Response: {resp.text[:500]}"
    )


def _generate_session(request_token: str) -> str:
    """Exchange request_token for access_token via KiteConnect."""
    logger.info("Step 3: Generating Kite session...")
    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]
    logger.info("Session generated successfully.")
    return access_token


def _save_token(access_token: str):
    """Save token with today's date so main.py can reuse it."""
    os.makedirs("logs", exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(f"{date.today()}\n{access_token}\n")
    logger.info(f"Token saved to {TOKEN_FILE}")


def run_auto_auth():
    logger.info("=" * 50)
    logger.info("  Kite Auto-Login starting")
    logger.info("=" * 50)

    _validate_env()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "X-Kite-Version": "3",
    })

    try:
        request_id    = _login_step1(session)
        # Brief pause — Zerodha sometimes rejects TOTP if submitted too fast
        time.sleep(1)
        request_token = _login_step2(session, request_id)
        access_token  = _generate_session(request_token)
        _save_token(access_token)

        logger.info("=" * 50)
        logger.info("  AUTO-LOGIN SUCCESSFUL")
        logger.info(f"  Token valid for today: {date.today()}")
        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"AUTO-LOGIN FAILED: {e}")
        logger.error("Bot will NOT be able to place live orders today.")
        logger.error("Check credentials and TOTP secret in .env")
        sys.exit(1)


if __name__ == "__main__":
    run_auto_auth()
