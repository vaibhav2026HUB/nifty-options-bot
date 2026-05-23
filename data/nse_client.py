"""
NSE India free data client — no API key required.

Fetches:
  - Nifty 50 spot price
  - Nifty 50 previous day close
  - India VIX
  - Option chain (LTPs for spread premium calculation)
  - Weekly expiry dates

NSE requires a browser-like session (cookies). We initialise by hitting the
homepage first, then reuse those cookies for all subsequent API calls.
"""

import time
import logging
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com",
    "Connection":      "keep-alive",
}


class NSEClient:
    BASE = "https://www.nseindia.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)
        self._refresh_session()

    # ─── Session management ────────────────────────────────────────────────────

    def _refresh_session(self):
        """Hit NSE homepage to acquire required cookies."""
        try:
            self.session.get(self.BASE, timeout=15)
            time.sleep(1.5)
            logger.debug("NSE session refreshed.")
        except Exception as e:
            logger.error(f"NSE session refresh failed: {e}")

    def _get(self, url: str, retries: int = 3) -> dict:
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code in (401, 403):
                    logger.warning(f"NSE returned {resp.status_code} — refreshing session.")
                    self._refresh_session()
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.warning(f"NSE GET attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    time.sleep(2 ** attempt)
        raise ConnectionError(f"NSE API unavailable after {retries} attempts: {url}")

    # ─── Public data methods ───────────────────────────────────────────────────

    def get_option_chain(self) -> dict:
        """Fetch full Nifty option chain from NSE."""
        url = f"{self.BASE}/api/option-chain-indices?symbol=NIFTY"
        return self._get(url)

    def get_nifty_spot(self) -> float:
        """Return current Nifty 50 spot price from allIndices."""
        url  = f"{self.BASE}/api/allIndices"
        data = self._get(url)
        for item in data.get("data", []):
            if item.get("index") == "NIFTY 50":
                spot = float(item["last"])
                logger.debug(f"Nifty spot: {spot}")
                return spot
        raise ValueError("Nifty 50 spot not found in NSE allIndices response.")

    def get_prev_close(self) -> float:
        """
        Return Nifty 50 previous trading day closing price.
        Fetched from NSE allIndices endpoint which provides previousClose field.
        """
        url  = f"{self.BASE}/api/allIndices"
        data = self._get(url)
        for item in data.get("data", []):
            if item.get("index") == "NIFTY 50":
                prev_close = float(item["previousClose"])
                logger.info(f"Nifty prev close fetched from NSE: {prev_close}")
                return prev_close
        raise ValueError(
            "Nifty 50 previousClose not found in NSE allIndices response. "
            "Check NSE API response format."
        )

    def get_india_vix(self) -> float:
        """Return current India VIX value."""
        url  = f"{self.BASE}/api/allIndices"
        data = self._get(url)
        for item in data.get("data", []):
            if item.get("index") == "INDIA VIX":
                vix = float(item["last"])
                logger.debug(f"India VIX: {vix}")
                return vix
        raise ValueError("India VIX not found in NSE allIndices response.")

    def get_weekly_expiry(self) -> str:
        """
        Return the nearest weekly expiry that is strictly in the future.
        Format: 'DD-Mon-YYYY' e.g. '25-Mar-2026'.

        Primary: reads NSE option chain expiry list (live trading days only).
        Fallback: computes next Tuesday (confirmed Nifty weekly expiry day as of Mar 2026).
                  Used on weekends / when option chain is unavailable.

        If today IS the expiry day we skip it (high gamma risk, per our rules).
        """
        today = date.today()
        try:
            data         = self.get_option_chain()
            expiry_dates = data.get("records", {}).get("expiryDates", [])
            for expiry_str in expiry_dates:
                expiry_date = datetime.strptime(expiry_str, "%d-%b-%Y").date()
                if expiry_date > today:
                    logger.info(f"Weekly expiry from NSE: {expiry_str} ({expiry_date.strftime('%A')})")
                    return expiry_str
        except Exception as e:
            logger.warning(f"Option chain unavailable ({e}), using calendar fallback.")

        # Fallback: find next Tuesday (weekday=1) after today
        EXPIRY_WEEKDAY = 1  # Tuesday — confirmed from Kite Mar 2026
        d = today + timedelta(days=1)
        while d.weekday() != EXPIRY_WEEKDAY:
            d += timedelta(days=1)
        # NSE format: no leading zero on day e.g. '25-Mar-2026' not '05-Mar-2026'
        expiry_str = f"{d.day}-{d.strftime('%b-%Y')}"
        logger.info(f"Weekly expiry (calendar fallback): {expiry_str} ({d.strftime('%A')})")
        return expiry_str

    def get_option_ltp(self, strike: int, option_type: str, expiry: str) -> float:
        """
        Return Last Traded Price for a specific option contract.

        Args:
            strike:      e.g. 23000
            option_type: 'CE' or 'PE'
            expiry:      must match NSE format exactly (e.g. '27-Mar-2026')

        Raises:
            ValueError if the contract is not found in the option chain.
        """
        data = self.get_option_chain()
        for record in data["records"]["data"]:
            if (
                record["strikePrice"] == strike
                and record.get("expiryDate") == expiry
                and option_type in record
            ):
                ltp = float(record[option_type].get("lastPrice", 0.0))
                logger.debug(f"LTP {option_type} {strike} {expiry}: {ltp}")
                return ltp

        raise ValueError(
            f"Option not found in chain: {option_type} {strike} expiry={expiry}. "
            f"Verify strike and expiry format match NSE exactly."
        )

    def get_spread_value(
        self, buy_strike: int, sell_strike: int, option_type: str, expiry: str
    ) -> float:
        """
        Return current spread value = buy_leg_LTP - sell_leg_LTP.
        Makes a single option chain call to fetch both legs efficiently.

        Raises:
            ValueError if either leg is not found.
        """
        data     = self.get_option_chain()
        buy_ltp  = None
        sell_ltp = None

        for record in data["records"]["data"]:
            if record.get("expiryDate") != expiry:
                continue
            if option_type not in record:
                continue
            s = record["strikePrice"]
            if s == buy_strike:
                buy_ltp  = float(record[option_type].get("lastPrice", 0.0))
            elif s == sell_strike:
                sell_ltp = float(record[option_type].get("lastPrice", 0.0))

        if buy_ltp is None:
            raise ValueError(f"Buy leg not found: {option_type} {buy_strike} {expiry}")
        if sell_ltp is None:
            raise ValueError(f"Sell leg not found: {option_type} {sell_strike} {expiry}")

        spread = buy_ltp - sell_ltp
        logger.debug(f"Spread: buy={buy_ltp} sell={sell_ltp} spread={spread:.2f}")
        return spread
