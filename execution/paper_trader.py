"""
Paper trading engine — full strategy simulation, zero real orders.

Fills are simulated at Last Traded Price (LTP) from NSE.
Capital state persists across days in logs/state.json.
All trades are logged to logs/trades.csv.
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


class PaperTrader:

    def __init__(self, nse_client, risk_manager: RiskManager):
        self.nse  = nse_client
        self.risk = risk_manager
        self._ensure_log_file()

    # ─── Entry ────────────────────────────────────────────────────────────────

    def enter(self, spread_order, signal) -> bool:
        """
        Simulates a spread entry at current LTP.
        Returns True on success.
        """
        position = {
            "direction":         spread_order.direction,
            "option_type":       spread_order.option_type,
            "buy_strike":        spread_order.buy_strike,
            "sell_strike":       spread_order.sell_strike,
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
            f"[PAPER] ENTRY — {spread_order.direction.upper()} SPREAD\n"
            f"Buy  {spread_order.option_type} {spread_order.buy_strike}\n"
            f"Sell {spread_order.option_type} {spread_order.sell_strike}\n"
            f"Expiry: {spread_order.expiry}\n"
            f"Net debit: Rs.{spread_order.entry_premium:.2f}/unit "
            f"(Rs.{spread_order.total_debit:.2f} total)\n"
            f"Qty: {spread_order.qty} units | VIX: {signal.vix:.1f}"
        )
        logger.info(msg)
        send_alert(msg)
        return True

    # ─── Exit checks ──────────────────────────────────────────────────────────

    def check_exits(self) -> Optional[str]:
        """
        Evaluates all exit conditions. Returns exit_reason string if exited, else None.
        Call this every 60 seconds during market hours.
        """
        position = self.risk.get_open_position()
        if not position:
            return None

        # Use IST-aware time for all time checks
        now = datetime.now(IST)

        # Force exit at configured time (default 3:00 PM, 2:45 PM on GitHub Actions)
        if now.hour > config.FORCE_EXIT_HOUR or (now.hour == config.FORCE_EXIT_HOUR and now.minute >= config.FORCE_EXIT_MINUTE):
            self._exit(position, "force_exit_3pm", exit_premium=self._current_spread(position))
            return "force_exit_3pm"

        current_spread = self._current_spread(position)
        entry_premium  = position["entry_premium"]
        pt_mult        = 1 + position.get("profit_target_pct", 1.00)
        sl_pct         = position.get("stop_loss_pct", 0.50)

        if current_spread >= pt_mult * entry_premium:
            self._exit(position, "profit_target", current_spread)
            return "profit_target"

        if current_spread <= sl_pct * entry_premium:
            self._exit(position, "stop_loss", current_spread)
            return "stop_loss"

        # Spot move stop: 0.5% adverse move
        current_spot = self.nse.get_nifty_spot()
        spot_move    = (current_spot - position["entry_spot"]) / position["entry_spot"]

        if position["direction"] == "bull" and spot_move <= -config.SPOT_MOVE_STOP_PCT:
            self._exit(position, "spot_stop_adverse", current_spread)
            return "spot_stop_adverse"

        if position["direction"] == "bear" and spot_move >= config.SPOT_MOVE_STOP_PCT:
            self._exit(position, "spot_stop_adverse", current_spread)
            return "spot_stop_adverse"

        logger.debug(
            f"[PAPER] Position OK — spread={current_spread:.2f} "
            f"(entry={entry_premium:.2f}) spot={current_spot:.0f}"
        )
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
        entry_premium = position["entry_premium"]
        qty           = position["qty"]
        pnl           = round((exit_premium - entry_premium) * qty, 2)
        capital       = self.risk.record_trade_close(pnl)

        self._log_trade(position, exit_premium, reason, pnl)

        import journal
        journal.log_trade_close(reason, exit_premium, pnl, capital)

        msg = (
            f"[PAPER] EXIT — {reason.upper()}\n"
            f"Entry: Rs.{entry_premium:.2f} | Exit: Rs.{exit_premium:.2f}\n"
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

        row = [
            date.today(),
            "NIFTY",
            position["direction"],
            round(position["vix_at_entry"], 2),
            position["buy_strike"],
            position["sell_strike"],
            round(position["entry_premium"], 2),
            round(exit_premium, 2),
            exit_reason,
            pnl,
            market_condition,
            f"[PAPER] qty={position['qty']} lot_mult={position['lot_multiplier']}",
        ]
        with open(config.TRADE_LOG_PATH, "a", newline="") as f:
            csv.writer(f).writerow(row)
        logger.info(f"Trade logged to {config.TRADE_LOG_PATH}")

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
