"""
Live trading engine — places real orders via Kite Connect.

SAFETY CHECKLIST before enabling:
  [ ] Paper trading ran for at least 2 weeks
  [ ] All exit conditions verified in paper mode
  [ ] KITE_API_KEY and KITE_API_SECRET set in .env
  [ ] kite_token.txt refreshed today (run: python auth.py)
  [ ] PAPER_TRADING=False set in .env
  [ ] You understand this places REAL orders with REAL money

Both legs are placed as sequential market orders (Kite personal API does not
support true basket orders). If the buy leg fills but the sell leg fails,
an urgent alert is sent and manual intervention is required.

Kite NFO symbol format for weekly Nifty options: NIFTY{DDMMMYY}{STRIKE}{TYPE}
Example: NIFTY27MAR2623000CE
Verify this against actual symbols in your Kite account before going live.
"""

import csv
import logging
import os
from datetime import date, datetime
from typing import Optional

import pytz

import config
from risk.risk_manager import RiskManager
from alerts.telegram_bot import send_alert

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class LiveTrader:

    def __init__(self, kite, nse_client, risk_manager: RiskManager):
        """
        kite:        Authenticated KiteConnect instance (from auth.py)
        nse_client:  NSEClient for live price checks
        risk_manager: RiskManager instance
        """
        self.kite = kite
        self.nse  = nse_client
        self.risk = risk_manager
        self._ensure_log_file()

    # ─── Entry ────────────────────────────────────────────────────────────────

    def enter(self, spread_order, signal) -> bool:
        """
        Places buy leg then sell leg as market orders.
        Returns True only if BOTH legs succeed.
        On any failure, sends urgent alert for manual intervention.
        """
        try:
            expiry_code = _kite_expiry_code(spread_order.expiry)
            buy_sym  = f"NIFTY{expiry_code}{spread_order.buy_strike}{spread_order.option_type}"
            sell_sym = f"NIFTY{expiry_code}{spread_order.sell_strike}{spread_order.option_type}"

            logger.info(f"[LIVE] Placing BUY  leg: {buy_sym}  qty={spread_order.qty}")
            buy_oid = self.kite.place_order(
                variety=          self.kite.VARIETY_REGULAR,
                exchange=         self.kite.EXCHANGE_NFO,
                tradingsymbol=    buy_sym,
                transaction_type= self.kite.TRANSACTION_TYPE_BUY,
                quantity=         spread_order.qty,
                order_type=       self.kite.ORDER_TYPE_MARKET,
                product=          self.kite.PRODUCT_MIS,
            )

            # Validate buy order was accepted before placing sell
            if not buy_oid:
                raise ValueError(f"Buy order returned no order ID for {buy_sym}")

            logger.info(f"[LIVE] BUY leg accepted. OrderID={buy_oid}")

            logger.info(f"[LIVE] Placing SELL leg: {sell_sym}  qty={spread_order.qty}")
            sell_oid = self.kite.place_order(
                variety=          self.kite.VARIETY_REGULAR,
                exchange=         self.kite.EXCHANGE_NFO,
                tradingsymbol=    sell_sym,
                transaction_type= self.kite.TRANSACTION_TYPE_SELL,
                quantity=         spread_order.qty,
                order_type=       self.kite.ORDER_TYPE_MARKET,
                product=          self.kite.PRODUCT_MIS,
            )

            if not sell_oid:
                raise ValueError(
                    f"Sell order returned no order ID for {sell_sym}. "
                    f"BUY LEG {buy_sym} IS OPEN — CLOSE MANUALLY."
                )

            logger.info(f"[LIVE] SELL leg accepted. OrderID={sell_oid}")

        except Exception as e:
            logger.error(f"[LIVE] ENTRY FAILED: {e}")
            send_alert(f"URGENT [LIVE] ENTRY FAILED — MANUAL CHECK REQUIRED\n{e}")
            return False

        position = {
            "direction":      spread_order.direction,
            "option_type":    spread_order.option_type,
            "buy_strike":     spread_order.buy_strike,
            "sell_strike":    spread_order.sell_strike,
            "buy_symbol":     buy_sym,
            "sell_symbol":    sell_sym,
            "buy_order_id":   buy_oid,
            "sell_order_id":  sell_oid,
            "expiry":         spread_order.expiry,
            "entry_premium":  spread_order.entry_premium,
            "entry_spot":     signal.spot,
            "qty":            spread_order.qty,
            "lot_multiplier": spread_order.lot_multiplier,
            "vix_at_entry":   signal.vix,
            "entry_time":     str(datetime.now(IST)),
        }
        self.risk.record_trade_open(position)

        msg = (
            f"[LIVE] ENTRY — {spread_order.direction.upper()} SPREAD\n"
            f"Buy  {buy_sym}  (OrderID: {buy_oid})\n"
            f"Sell {sell_sym}  (OrderID: {sell_oid})\n"
            f"Qty: {spread_order.qty}  VIX: {signal.vix:.1f}\n"
            f"Net debit: Rs.{spread_order.entry_premium:.2f}/unit"
        )
        logger.info(msg)
        send_alert(msg)
        return True

    # ─── Exit checks ──────────────────────────────────────────────────────────

    def check_exits(self) -> Optional[str]:
        """Same exit logic as paper trader — places real closing orders."""
        position = self.risk.get_open_position()
        if not position:
            return None

        # Use IST-aware time for all time checks
        now = datetime.now(IST)

        if now.hour >= 15:
            spread = self._current_spread(position)
            self._exit(position, "force_exit_3pm", spread)
            return "force_exit_3pm"

        current_spread = self._current_spread(position)
        entry_premium  = position["entry_premium"]

        if current_spread >= config.PROFIT_TARGET_MULTIPLIER * entry_premium:
            self._exit(position, "profit_target", current_spread)
            return "profit_target"

        if current_spread <= config.STOP_LOSS_MULTIPLIER * entry_premium:
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

        return None

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _current_spread(self, position: dict) -> float:
        return self.nse.get_spread_value(
            position["buy_strike"],
            position["sell_strike"],
            position["option_type"],
            position["expiry"],
        )

    def _exit(self, position: dict, reason: str, exit_premium: float):
        buy_sym  = position.get("buy_symbol",  "")
        sell_sym = position.get("sell_symbol", "")

        try:
            # Close buy leg (sell to close)
            self.kite.place_order(
                variety=          self.kite.VARIETY_REGULAR,
                exchange=         self.kite.EXCHANGE_NFO,
                tradingsymbol=    buy_sym,
                transaction_type= self.kite.TRANSACTION_TYPE_SELL,
                quantity=         position["qty"],
                order_type=       self.kite.ORDER_TYPE_MARKET,
                product=          self.kite.PRODUCT_MIS,
            )
            # Close sell leg (buy to close)
            self.kite.place_order(
                variety=          self.kite.VARIETY_REGULAR,
                exchange=         self.kite.EXCHANGE_NFO,
                tradingsymbol=    sell_sym,
                transaction_type= self.kite.TRANSACTION_TYPE_BUY,
                quantity=         position["qty"],
                order_type=       self.kite.ORDER_TYPE_MARKET,
                product=          self.kite.PRODUCT_MIS,
            )
        except Exception as e:
            logger.error(f"[LIVE] EXIT ORDERS FAILED: {e}")
            send_alert(f"URGENT [LIVE] EXIT ORDERS FAILED — CLOSE MANUALLY\n{e}")
            return

        pnl     = round((exit_premium - position["entry_premium"]) * position["qty"], 2)
        capital = self.risk.record_trade_close(pnl)
        self._log_trade(position, exit_premium, reason, pnl)

        import journal
        journal.log_trade_close(reason, exit_premium, pnl, capital)

        msg = (
            f"[LIVE] EXIT — {reason.upper()}\n"
            f"Entry: Rs.{position['entry_premium']:.2f} | Exit: Rs.{exit_premium:.2f}\n"
            f"P&L: Rs.{pnl:+.2f} | Capital: Rs.{capital:.2f}"
        )
        logger.info(msg)
        send_alert(msg)

    def _log_trade(self, position: dict, exit_premium: float, exit_reason: str, pnl: float):
        vix_level = position["vix_at_entry"]
        if vix_level <= config.VIX_FULL_SIZE_MAX:
            market_condition = "low_vix_full_size"
        elif vix_level <= config.VIX_HALF_SIZE_MAX:
            market_condition = "mid_vix_half_size"
        else:
            market_condition = "high_vix_no_trade"

        with open(config.TRADE_LOG_PATH, "a", newline="") as f:
            csv.writer(f).writerow([
                date.today(), "NIFTY", position["direction"],
                round(position["vix_at_entry"], 2),
                position["buy_strike"], position["sell_strike"],
                round(position["entry_premium"], 2), round(exit_premium, 2),
                exit_reason, pnl, market_condition,
                f"[LIVE] qty={position['qty']} lot_mult={position['lot_multiplier']}",
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


def _kite_expiry_code(expiry_str: str) -> str:
    """
    Converts NSE expiry '27-Mar-2026' to Kite NFO weekly symbol component.

    Kite weekly Nifty format: NIFTY{DDMMMYY}{STRIKE}{TYPE}
    Example: '27-Mar-2026' -> '27MAR26' -> symbol NIFTY27MAR2623000CE

    IMPORTANT: Verify this against actual symbols in your Kite account.
    Log in to kite.zerodha.com -> Positions -> search NIFTY to see live symbol format.
    If format differs, update this function before going live.
    """
    dt = datetime.strptime(expiry_str, "%d-%b-%Y")
    return dt.strftime("%d%b%y").upper()   # DDMMMYY e.g. '27MAR26'
