"""
Upstox live trading engine — places real spread orders via Upstox API v2.

Mirrors live_trader.py (Kite) but uses Upstox REST API directly with requests.
No SDK dependency — requests is already installed.

Entry flow:
  1. Fetch instrument_keys from Upstox option chain API (by strike/expiry/type)
  2. Fetch live LTPs and verify net debit ≤ signal.max_premium
  3. Place BUY leg (market order, intraday product)
  4. Verify order ID — abort if missing
  5. Place SELL leg — urgent alert + log half-open state if this fails
  6. Record position, send confirmation email

Exit flow (called every 60 seconds):
  - Profit target, stop loss, spot-move stop, force exit at FORCE_EXIT_TIME
  - Fetches live spread value from Upstox LTP API for accurate P&L
  - Sends urgent email if exit orders fail (bot retries every 60s until 3:30 PM)

SAFETY NOTE: Upstox intraday product "I" auto-squares off at 3:20 PM if
our exit orders somehow fail. This is a last-resort safety net.
"""

import csv
import logging
import os
import requests
from datetime import date, datetime
from typing import Optional, Tuple

import pytz

import config
from risk.risk_manager import RiskManager
from alerts.notifier import send_alert

logger = logging.getLogger(__name__)
IST     = pytz.timezone("Asia/Kolkata")
API_URL = "https://api.upstox.com/v2"


class UpstoxTrader:

    def __init__(self, access_token: str, nse_client, risk_manager: RiskManager):
        self.token = access_token
        self.nse   = nse_client
        self.risk  = risk_manager
        self._ensure_log_file()

    # ─── Entry ────────────────────────────────────────────────────────────────

    def enter(self, spread_order, signal) -> bool:
        try:
            expiry_upstox = _nse_to_upstox_date(spread_order.expiry)

            buy_key, sell_key = self._get_instrument_keys(
                spread_order.buy_strike,
                spread_order.sell_strike,
                spread_order.option_type,
                expiry_upstox,
            )

            buy_ltp, sell_ltp = self._get_ltps(buy_key, sell_key)
            net_debit = round(buy_ltp - sell_ltp, 2)
            logger.info(
                f"[UPSTOX] Pre-trade: buy_ltp={buy_ltp:.2f}  sell_ltp={sell_ltp:.2f}  "
                f"net_debit=Rs.{net_debit:.2f}  limit=Rs.{signal.max_premium}"
            )

            if net_debit > signal.max_premium:
                send_alert(
                    f"[BOT] Spread rejected — net debit Rs.{net_debit:.2f} "
                    f"> limit Rs.{signal.max_premium}. No trade today."
                )
                return False
            if net_debit <= 0:
                send_alert(
                    f"[BOT] Spread rejected — invalid net debit Rs.{net_debit:.2f}. "
                    "Spread may be deep OTM or illiquid. No trade today."
                )
                return False

            spread_order.entry_premium = net_debit
            spread_order.total_debit   = round(net_debit * spread_order.qty, 2)

            logger.info(f"[UPSTOX] Placing BUY  leg: {buy_key}  qty={spread_order.qty}")
            buy_oid = self._place_order("BUY", buy_key, spread_order.qty)
            if not buy_oid:
                raise ValueError(f"BUY leg returned no order ID for {buy_key}")
            logger.info(f"[UPSTOX] BUY leg accepted. OrderID={buy_oid}")

            logger.info(f"[UPSTOX] Placing SELL leg: {sell_key}  qty={spread_order.qty}")
            sell_oid = self._place_order("SELL", sell_key, spread_order.qty)
            if not sell_oid:
                raise ValueError(
                    f"SELL leg returned no order ID for {sell_key}. "
                    f"BUY LEG {buy_key} IS OPEN — CLOSE MANUALLY ON UPSTOX NOW."
                )
            logger.info(f"[UPSTOX] SELL leg accepted. OrderID={sell_oid}")

        except Exception as e:
            logger.error(f"[UPSTOX] ENTRY FAILED: {e}")
            send_alert(f"URGENT [UPSTOX] ENTRY FAILED — MANUAL CHECK REQUIRED\n{e}")
            return False

        position = {
            "direction":         spread_order.direction,
            "option_type":       spread_order.option_type,
            "buy_strike":        spread_order.buy_strike,
            "sell_strike":       spread_order.sell_strike,
            "buy_key":           buy_key,
            "sell_key":          sell_key,
            "buy_order_id":      buy_oid,
            "sell_order_id":     sell_oid,
            "expiry":            spread_order.expiry,
            "entry_premium":     spread_order.entry_premium,
            "entry_spot":        signal.spot,
            "qty":               spread_order.qty,
            "vix_at_entry":      signal.vix,
            "stop_loss_pct":     signal.stop_loss_pct,
            "profit_target_pct": signal.profit_target_pct,
            "entry_time":        str(datetime.now(IST)),
        }
        self.risk.record_trade_open(position)

        msg = (
            f"[UPSTOX] ENTRY — {spread_order.direction.upper()} SPREAD\n"
            f"Buy  {buy_key}  (OrderID: {buy_oid})\n"
            f"Sell {sell_key}  (OrderID: {sell_oid})\n"
            f"Qty: {spread_order.qty}  VIX: {signal.vix:.1f}\n"
            f"Net debit: Rs.{spread_order.entry_premium:.2f}/unit  "
            f"Total outflow: Rs.{spread_order.total_debit:.2f}"
        )
        logger.info(msg)
        send_alert(msg)
        return True

    # ─── Exit checks ──────────────────────────────────────────────────────────

    def check_exits(self) -> Optional[str]:
        position = self.risk.get_open_position()
        if not position:
            return None

        now = datetime.now(IST)

        if now.hour > config.FORCE_EXIT_HOUR or (
            now.hour == config.FORCE_EXIT_HOUR and now.minute >= config.FORCE_EXIT_MINUTE
        ):
            spread = self._current_spread(position)
            self._exit(position, "force_exit", spread)
            return "force_exit"

        current_spread = self._current_spread(position)
        entry_premium  = position["entry_premium"]
        pt_mult = 1 + position.get("profit_target_pct", 1.00)
        sl_pct  = position.get("stop_loss_pct", 0.50)

        if current_spread >= pt_mult * entry_premium:
            self._exit(position, "profit_target", current_spread)
            return "profit_target"

        if current_spread <= sl_pct * entry_premium:
            self._exit(position, "stop_loss", current_spread)
            return "stop_loss"

        current_spot = self.nse.get_nifty_spot()
        spot_move    = (current_spot - position["entry_spot"]) / position["entry_spot"]

        if position["direction"] == "bull" and spot_move <= -config.SPOT_MOVE_STOP_PCT:
            self._exit(position, "spot_stop_adverse", current_spread)
            return "spot_stop_adverse"

        if position["direction"] == "bear" and spot_move >= config.SPOT_MOVE_STOP_PCT:
            self._exit(position, "spot_stop_adverse", current_spread)
            return "spot_stop_adverse"

        logger.debug(
            f"[UPSTOX] Position OK — spread={current_spread:.2f} "
            f"(entry={entry_premium:.2f}) spot={current_spot:.0f}"
        )
        return None

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept":        "application/json",
        }

    def _get_instrument_keys(
        self, buy_strike: int, sell_strike: int, option_type: str, expiry: str
    ) -> Tuple[str, str]:
        """Fetch Upstox instrument_keys for both legs from the option chain API."""
        resp = requests.get(
            f"{API_URL}/option/chain",
            headers=self._headers(),
            params={"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": expiry},
            timeout=15,
        )
        resp.raise_for_status()

        key_field = "call_options" if option_type == "CE" else "put_options"
        buy_key = sell_key = None

        for entry in resp.json().get("data", []):
            strike = entry.get("strike_price")
            ikey   = entry.get(key_field, {}).get("instrument_key")
            if ikey:
                if strike == buy_strike:
                    buy_key = ikey
                elif strike == sell_strike:
                    sell_key = ikey
            if buy_key and sell_key:
                break

        if not buy_key:
            raise ValueError(
                f"Instrument key not found for {option_type} {buy_strike} "
                f"expiry={expiry}. Check that the expiry is correct."
            )
        if not sell_key:
            raise ValueError(
                f"Instrument key not found for {option_type} {sell_strike} "
                f"expiry={expiry}."
            )
        return buy_key, sell_key

    def _get_ltps(self, buy_key: str, sell_key: str) -> Tuple[float, float]:
        """Fetch live LTPs for both legs in one API call."""
        # Build URL manually — requests encodes '|' as %7C which Upstox rejects
        url = f"{API_URL}/market-quote/ltp?instrument_key={buy_key},{sell_key}"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        logger.info(f"[UPSTOX] LTP response keys: {list(data.keys())}")

        def _ltp(key: str) -> float:
            val = data.get(key) or data.get(key.replace("|", ":")) or {}
            ltp = float(val.get("last_price", 0.0))
            logger.info(f"[UPSTOX] LTP {key}: {ltp}")
            return ltp

        return _ltp(buy_key), _ltp(sell_key)

    def _current_spread(self, position: dict) -> float:
        buy_ltp, sell_ltp = self._get_ltps(position["buy_key"], position["sell_key"])
        return buy_ltp - sell_ltp

    def _place_order(self, side: str, instrument_key: str, qty: int) -> Optional[str]:
        resp = requests.post(
            f"{API_URL}/order/place",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={
                "quantity":           qty,
                "product":            "I",       # I = Intraday (MIS equivalent)
                "validity":           "DAY",
                "price":              0,
                "tag":                "nifty_bot",
                "instrument_token":   instrument_key,
                "order_type":         "MARKET",
                "transaction_type":   side,
                "disclosed_quantity": 0,
                "trigger_price":      0,
                "is_amo":             False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("order_id")

    def _exit(self, position: dict, reason: str, exit_premium: float):
        buy_key  = position["buy_key"]
        sell_key = position["sell_key"]
        qty      = position["qty"]

        try:
            self._place_order("SELL", buy_key,  qty)  # close buy leg
            self._place_order("BUY",  sell_key, qty)  # close sell leg
        except Exception as e:
            logger.error(f"[UPSTOX] EXIT ORDERS FAILED: {e}")
            send_alert(
                f"URGENT [UPSTOX] EXIT ORDERS FAILED — CLOSE MANUALLY ON UPSTOX NOW\n"
                f"Reason: {reason}\n{e}"
            )
            return

        pnl     = round((exit_premium - position["entry_premium"]) * qty, 2)
        capital = self.risk.record_trade_close(pnl)
        self._log_trade(position, exit_premium, reason, pnl)

        import journal
        journal.log_trade_close(reason, exit_premium, pnl, capital)

        msg = (
            f"[UPSTOX] EXIT — {reason.upper()}\n"
            f"Entry: Rs.{position['entry_premium']:.2f} | Exit: Rs.{exit_premium:.2f}\n"
            f"P&L: Rs.{pnl:+.2f} | Capital: Rs.{capital:.2f}"
        )
        logger.info(msg)
        send_alert(msg)

    def _log_trade(self, position: dict, exit_premium: float, exit_reason: str, pnl: float):
        vix = position["vix_at_entry"]
        if vix <= 16:
            market_condition = "low_vix"
        elif vix <= 18:
            market_condition = "mid_vix"
        else:
            market_condition = "high_vix"

        with open(config.TRADE_LOG_PATH, "a", newline="") as f:
            csv.writer(f).writerow([
                date.today(), "NIFTY", position["direction"],
                round(vix, 2),
                position["buy_strike"], position["sell_strike"],
                round(position["entry_premium"], 2), round(exit_premium, 2),
                exit_reason, pnl, market_condition,
                f"[UPSTOX] qty={position['qty']}",
            ])

    def _ensure_log_file(self):
        os.makedirs(os.path.dirname(config.TRADE_LOG_PATH), exist_ok=True)
        if not os.path.exists(config.TRADE_LOG_PATH):
            with open(config.TRADE_LOG_PATH, "w", newline="") as f:
                csv.writer(f).writerow([
                    "date", "index", "direction", "vix_at_entry",
                    "buy_strike", "sell_strike", "entry_premium",
                    "exit_premium", "exit_reason", "pnl",
                    "market_condition", "notes",
                ])


def _nse_to_upstox_date(nse_date: str) -> str:
    """Convert NSE format '29-May-2025' → Upstox format '2025-05-29'."""
    dt = datetime.strptime(nse_date, "%d-%b-%Y")
    return dt.strftime("%Y-%m-%d")
