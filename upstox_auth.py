"""
Upstox OAuth2 authentication with daily token persistence.

Automated flow (headless / GitHub Actions):
  1. Playwright opens the Upstox auth URL in a headless Chromium browser
  2. Fills mobile number → PIN → TOTP automatically
  3. Intercepts the OAuth redirect to extract the one-time auth code
  4. POSTs to Upstox token endpoint → receives access_token
  5. Saves upstox_token.txt as: {today's date}\\n{access_token}

Token is valid until 6 AM IST next day. Loaded from file on subsequent calls
so Playwright only runs once per day.

Required env vars (GitHub Secrets or .env):
  UPSTOX_API_KEY        from developer.upstox.com → Your Apps → API Key
  UPSTOX_API_SECRET     from developer.upstox.com → Your Apps → API Secret
  UPSTOX_REDIRECT_URI   must match what you registered in the developer console
                        (use http://127.0.0.1 — exactly as registered)
  UPSTOX_MOBILE         your Upstox registered mobile number (digits only)
  UPSTOX_PIN            your 6-digit Upstox PIN
  UPSTOX_TOTP_SECRET    TOTP secret key — from Upstox app → My Profile →
                        Two Factor Auth → "Can't scan? Use text key"

On auth failure: returns None. main.py falls back to ManualTrader automatically.
"""

import logging
import os
import time
import requests
import pyotp
from datetime import date
from urllib.parse import urlparse, parse_qs

import config

logger = logging.getLogger(__name__)

TOKEN_FILE = "upstox_token.txt"
TOKEN_URL  = "https://api.upstox.com/v2/login/authorization/token"
AUTH_BASE  = "https://api.upstox.com/v2/login/authorization/dialog"


def get_upstox_token() -> str | None:
    """Return a valid access token for today, or None if auth fails.

    Priority order:
      1. UPSTOX_ACCESS_TOKEN env var — manual override, bypasses Playwright entirely.
         Set this GitHub Secret if Playwright ever breaks. Generate the value by
         running `python upstox_auth.py` locally and copying the token from
         upstox_token.txt (3rd line).
      2. upstox_token.txt cached from today's earlier run.
      3. Fresh Playwright OAuth login.
    """
    override = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
    if override:
        logger.info("Using UPSTOX_ACCESS_TOKEN from environment (Playwright bypassed).")
        return override

    token = _load_token()
    if token:
        logger.info("Loaded today's Upstox token from upstox_token.txt.")
        return token

    logger.info("No valid token found — starting OAuth login via Playwright.")
    try:
        token = _do_oauth_login()
        _save_token(token)
        logger.info("Upstox access token obtained and saved.")
        return token
    except Exception as e:
        logger.error(f"Upstox auth failed: {e}")
        return None


def _fill_robust(page, selectors: list, value: str, step: str) -> None:
    """Try each selector in order. Takes a screenshot and raises if all fail."""
    import os as _os
    _os.makedirs("logs", exist_ok=True)
    for sel in selectors:
        try:
            page.fill(sel, value, timeout=4000)
            logger.info(f"[AUTH] {step} filled via selector: {sel}")
            return
        except Exception:
            continue
    page.screenshot(path=f"logs/auth_fail_{step}.png")
    raise RuntimeError(
        f"[AUTH] Could not fill {step} — all selectors failed: {selectors}\n"
        f"Screenshot saved to logs/auth_fail_{step}.png\n"
        f"Page URL: {page.url}"
    )


def _do_oauth_login() -> str:
    """Playwright headless OAuth flow. Returns access_token string."""
    import os as _os
    _os.makedirs("logs", exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Run: pip install playwright && playwright install chromium\n"
            "Or add playwright to requirements.txt and install before running."
        )

    api_key      = config.UPSTOX_API_KEY
    api_secret   = config.UPSTOX_API_SECRET
    redirect_uri = config.UPSTOX_REDIRECT_URI
    mobile       = config.UPSTOX_MOBILE
    pin          = config.UPSTOX_PIN
    totp_secret  = config.UPSTOX_TOTP_SECRET

    missing = [k for k, v in {
        "UPSTOX_API_KEY": api_key, "UPSTOX_API_SECRET": api_secret,
        "UPSTOX_REDIRECT_URI": redirect_uri, "UPSTOX_MOBILE": mobile,
        "UPSTOX_PIN": pin, "UPSTOX_TOTP_SECRET": totp_secret,
    }.items() if not v]
    if missing:
        raise ValueError(f"Missing env vars: {', '.join(missing)}")

    auth_url = (
        f"{AUTH_BASE}?response_type=code"
        f"&client_id={api_key}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=orders"
    )

    auth_code = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()

        def on_request(request):
            nonlocal auth_code
            url = request.url
            if url.startswith(redirect_uri) and "code=" in url:
                params = parse_qs(urlparse(url).query)
                code   = params.get("code", [None])[0]
                if code:
                    auth_code = code
                    logger.info("Auth code captured from callback redirect.")

        page.on("request", on_request)
        page.route("http://127.0.0.1/**", lambda route, req: route.abort())

        logger.info("Opening Upstox auth page...")
        page.goto(auth_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        page.screenshot(path="logs/auth_step1_loaded.png")

        # Step 1 — Mobile number (fallback selectors in priority order)
        _fill_robust(page, [
            "#mobileNum",
            "input[type='number']",
            "input[type='tel']",
            "input[placeholder*='mobile' i]",
            "input[placeholder*='number' i]",
        ], mobile, "mobile")
        page.wait_for_timeout(500)
        page.get_by_text("Get OTP").click()
        page.wait_for_timeout(3000)
        page.screenshot(path="logs/auth_step2_after_getotp.png")

        # Step 2 — TOTP (ensure code has at least 10s of life left)
        import time as _time
        remaining = 30 - (_time.time() % 30)
        if remaining < 10:
            logger.info(f"TOTP expires in {remaining:.0f}s — waiting for fresh code...")
            page.wait_for_timeout(int((remaining + 1) * 1000))
        totp = pyotp.TOTP(totp_secret).now()
        logger.info(f"TOTP generated with {30 - (_time.time() % 30):.0f}s remaining.")
        _fill_robust(page, [
            "#otpNum",
            "input[type='number'][maxlength='6']",
            "input[placeholder*='otp' i]",
            "input[placeholder*='code' i]",
        ], totp, "totp")
        page.wait_for_timeout(500)
        page.get_by_text("Continue").click()
        page.wait_for_timeout(4000)
        page.screenshot(path="logs/auth_step3_after_totp.png")

        # Step 3 — PIN
        try:
            page.wait_for_selector("#pinCode", timeout=15000)
        except Exception:
            pass  # selector might have changed — _fill_robust will handle it
        _fill_robust(page, [
            "#pinCode",
            "input[type='password']",
            "input[placeholder*='pin' i]",
            "input[placeholder*='passcode' i]",
        ], pin, "pin")
        page.wait_for_timeout(500)
        page.get_by_text("Continue").click()
        page.screenshot(path="logs/auth_step4_after_pin.png")

        # Wait for auth code capture (up to 15 seconds)
        for _ in range(30):
            if auth_code:
                break
            page.wait_for_timeout(500)

        browser.close()

    if not auth_code:
        raise RuntimeError(
            "OAuth redirect not captured — login may have failed.\n"
            "Check logs/auth_step*.png screenshots (uploaded as GitHub Actions artifacts)."
        )

    # Exchange auth code for access token
    payload = {
        "code":          auth_code,
        "client_id":     api_key,
        "client_secret": api_secret,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }
    resp = requests.post(
        TOKEN_URL,
        data=payload,
        headers={
            "Accept":       "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=15,
    )
    if not resp.ok:
        body = resp.text
        if "UDAPI100058" in body:
            raise RuntimeError(
                "Upstox account segments are inactive (UDAPI100058).\n"
                "Fix: Open Upstox app → Profile → Segments → reactivate F&O/Equity.\n"
                "Or log into upstox.com and follow the reactivation prompt."
            )
        raise RuntimeError(f"Token exchange failed {resp.status_code}: {body}")
    data = resp.json()

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Token endpoint returned no access_token. Response: {data}")
    return token


def get_public_ip() -> str:
    """Return current public IP. Falls back to empty string on failure."""
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return ""


def check_ip_changed() -> bool:
    """
    Returns True if the public IP has changed since the token was saved.
    Sends an urgent alert with the new IP and update instructions if so.
    """
    if not os.path.exists(TOKEN_FILE):
        return False
    with open(TOKEN_FILE) as f:
        lines = f.read().strip().splitlines()
    if len(lines) < 3:
        return False

    saved_ip = lines[1]
    current_ip = get_public_ip()

    if not current_ip or current_ip == saved_ip:
        return False

    msg = (
        f"URGENT [BOT] Public IP changed!\n"
        f"Old IP: {saved_ip}\n"
        f"New IP: {current_ip}\n\n"
        f"Action required before 9:20 AM:\n"
        f"1. Go to developer.upstox.com\n"
        f"2. Edit app NiftyBot → Primary IP\n"
        f"3. Replace with: {current_ip}\n"
        f"4. Save — bot will retry auth automatically."
    )
    logger.warning(msg)

    try:
        from alerts.notifier import send_alert
        send_alert(msg)
    except Exception:
        pass

    return True


def _load_token() -> str | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        lines = f.read().strip().splitlines()
    if len(lines) < 3:
        return None
    saved_date, saved_ip, token = lines[0], lines[1], lines[2]
    if saved_date != str(date.today()):
        logger.info("upstox_token.txt is from a previous day — fresh login required.")
        return None
    current_ip = get_public_ip()
    if current_ip and current_ip != saved_ip:
        logger.warning(f"IP changed ({saved_ip} → {current_ip}) — token invalidated, re-login required.")
        return None
    return token


def _save_token(token: str):
    ip = get_public_ip()
    with open(TOKEN_FILE, "w") as f:
        f.write(f"{date.today()}\n{ip}\n{token}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = get_upstox_token()
    if result:
        print("\nUpstox access token obtained successfully.")
        print("upstox_token.txt saved. Bot is ready to run.\n")
    else:
        print("\nERROR: Could not obtain Upstox access token. Check .env and try again.\n")
