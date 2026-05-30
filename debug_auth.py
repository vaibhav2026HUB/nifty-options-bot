"""Debug — full flow with request logging to capture redirect URL."""
import config, pyotp, time
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse, parse_qs

mobile      = config.UPSTOX_MOBILE
pin         = config.UPSTOX_PIN
totp_secret = config.UPSTOX_TOTP_SECRET
api_key     = config.UPSTOX_API_KEY
redirect_uri= config.UPSTOX_REDIRECT_URI

auth_url = (
    f"https://api.upstox.com/v2/login/authorization/dialog"
    f"?response_type=code&client_id={api_key}&redirect_uri={redirect_uri}"
)

captured_code = None

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page    = browser.new_page()

    # Log ALL requests — we'll see the redirect URL here
    def on_request(req):
        global captured_code
        url = req.url
        if "127.0.0.1" in url or "code=" in url:
            print(f"  >>> REDIRECT CAPTURED: {url}")
            if "code=" in url:
                params = parse_qs(urlparse(url).query)
                captured_code = params.get("code", [None])[0]
                print(f"  >>> AUTH CODE: {captured_code}")

    page.on("request", on_request)

    # Also abort 127.0.0.1 navigation so it doesn't error
    page.route("http://127.0.0.1/**", lambda route, req: route.abort())

    page.goto(auth_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    # Step 1 — Mobile
    page.fill("#mobileNum", mobile)
    page.wait_for_timeout(500)
    page.get_by_text("Get OTP").click()
    page.wait_for_timeout(5000)
    print(f"After Get OTP: {page.url}")

    # Step 2 — TOTP
    remaining = 30 - (time.time() % 30)
    if remaining < 10:
        print(f"Waiting {remaining:.0f}s for fresh TOTP...")
        page.wait_for_timeout(int((remaining + 1) * 1000))
    totp = pyotp.TOTP(totp_secret).now()
    print(f"TOTP: {totp}")
    page.fill("#otpNum", totp)
    page.wait_for_timeout(500)
    page.get_by_text("Continue").click()
    page.wait_for_timeout(4000)
    page.screenshot(path="debug_after_totp2.png")
    print(f"After TOTP: {page.url}")

    # Step 3 — PIN
    page.fill("#pinCode", pin)
    page.wait_for_timeout(500)
    page.get_by_text("Continue").click()

    # Wait up to 15 seconds for redirect
    for _ in range(30):
        if captured_code:
            break
        page.wait_for_timeout(500)

    page.screenshot(path="debug_after_pin.png")
    print(f"After PIN: {page.url}")
    browser.close()

if captured_code:
    print(f"\nSUCCESS — auth code captured: {captured_code[:10]}...")
else:
    print("\nFAILED — no auth code captured. Check screenshots.")
