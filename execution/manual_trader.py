"""
Manual trading engine — bot monitors, YOU place orders on Kite.

On entry:
  - Sends a push alert with exact order details to place on Kite app
  - Saves virtual position so bot can track exit conditions

On exit condition hit:
  - Sends a push alert telling you to exit, with exact order details
  - Logs P&L using the entry premium vs current spread value from NSE

You have 2 minutes to act on each alert before the window may move.
"""

import logging
from datetime import date, datetime
from typing import Optional

import pytz

import config
from risk.risk_manager import RiskManager
from alerts.notifier import send_alert

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def _kite_symbol(strike: int, option_type: str, expiry: str) -> str:
    """
    Formats the Kite trading symbol.
    e.g. NIFTY25MAR2623000CE
    expiry is like '25-Mar-2026'
    """
    dt = datetime.strptime(expiry, "%d-%b-%Y")
    code = dt.strftime("%d%b%y").upper()   # e.g. 25MAR26
    return f"NIFTY{code}{strike}{option_type}"


class ManualTrader:

    def __init__(self, nse_client, risk_manager: RiskManager):
        self.nse  = nse_client
        self.risk = risk_manager

    # ─── Entry ────────────────────────────────────────────────────────────────

    def enter(self, spread_order, signal) -> bool:
        """
        Records the virtual position and sends an entry alert.
        Returns True always (no real order to fail).
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

        buy_sym  = _kite_symbol(spread_order.buy_strike,  spread_order.option_type, spread_order.expiry)
        sell_sym = _kite_symbol(spread_order.sell_strike, spread_order.option_type, spread_order.expiry)
        qty      = spread_order.qty
        debit    = spread_order.entry_premium

        entry_spot  = signal.spot
        stop_nifty  = round(entry_spot * (1 - config.SPOT_MOVE_STOP_PCT))
        if signal.direction == "bull":
            target_nifty = spread_order.sell_strike  # spread near max when Nifty > sold strike
        else:
            target_nifty = spread_order.sell_strike  # spread near max when Nifty < sold strike

        if debit > 0:
            cost_line = f"Net cost : Rs.{debit:.2f}/unit  (Rs.{spread_order.total_debit:.2f} total)"
        else:
            cost_line = (
                f"Max net debit: Rs.{signal.max_premium:.0f}/unit  "
                f"(Rs.{signal.max_premium * qty:.0f} total)\n"
                f"Check on Kite — only place if spread cost is within this limit"
            )

        if signal.direction == "bull":
            stop_line   = f"Nifty BELOW {stop_nifty}  -> EXIT (stop loss)"
            target_line = f"Nifty ABOVE {target_nifty} -> EXIT (near max profit)"
        else:
            stop_line   = f"Nifty ABOVE {stop_nifty}  -> EXIT (stop loss)"
            target_line = f"Nifty BELOW {target_nifty} -> EXIT (near max profit)"

        alert = (
            f"TRADE NOW — {spread_order.direction.upper()} {spread_order.option_type} SPREAD\n"
            f"\n"
            f"On Kite place BOTH orders:\n"
            f"1. BUY  {buy_sym}  Qty:{qty}  MARKET\n"
            f"2. SELL {sell_sym}  Qty:{qty}  MARKET\n"
            f"\n"
            f"{cost_line}\n"
            f"Expiry : {spread_order.expiry} | VIX: {signal.vix:.1f}\n"
            f"\n"
            f"Set Kite alerts on NIFTY 50:\n"
            f"  {stop_line}\n"
            f"  {target_line}\n"
            f"  2:45 PM -> EXIT (bot will email you)\n"
            f"\n"
            f"To exit: SELL {buy_sym} + BUY {sell_sym}  Qty:{qty} each"
        )
        logger.info(alert)
        send_alert(alert, title="TRADE NOW", priority="urgent")
        return True

    # ─── Exit checks ──────────────────────────────────────────────────────────

    def check_exits(self) -> Optional[str]:
        """
        Evaluates exit conditions using live NSE prices.
        Sends an exit alert when a condition is hit.
        Call every 60 seconds during market hours.
        """
        position = self.risk.get_open_position()
        if not position:
            return None

        now = datetime.now(IST)

        is_force_exit_time = (
            now.hour > config.FORCE_EXIT_HOUR or
            (now.hour == config.FORCE_EXIT_HOUR and now.minute >= config.FORCE_EXIT_MINUTE)
        )

        # Try to get live spread value — NSE option chain may be unavailable on cloud
        try:
            current = self._current_spread(position)
        except Exception as e:
            logger.warning(f"Could not fetch spread value (NSE option chain unavailable): {e}")
            current = None

        if is_force_exit_time:
            self._exit(position, "force_exit_3pm", current or 0.0)
            return "force_exit_3pm"

        # Premium-based exits — only when option chain LTP is available
        if current is not None:
            entry   = position["entry_premium"]
            pt_mult = 1 + position.get("profit_target_pct", 1.00)
            sl_pct  = position.get("stop_loss_pct", 0.50)

            if entry > 0:
                if current >= pt_mult * entry:
                    self._exit(position, "profit_target", current)
                    return "profit_target"

                if current <= sl_pct * entry:
                    self._exit(position, "stop_loss", current)
                    return "stop_loss"

        # Spot move stop — uses allIndices endpoint which works from cloud even when option chain is blocked
        try:
            spot = self.nse.get_nifty_spot()
            move = (spot - position["entry_spot"]) / position["entry_spot"]
            if position["direction"] == "bull" and move <= -config.SPOT_MOVE_STOP_PCT:
                self._exit(position, "spot_stop_adverse", current or 0.0)
                return "spot_stop_adverse"
            if position["direction"] == "bear" and move >= config.SPOT_MOVE_STOP_PCT:
                self._exit(position, "spot_stop_adverse", current or 0.0)
                return "spot_stop_adverse"
        except Exception as e:
            logger.warning(f"Could not fetch spot for stop check: {e}")

        if current is not None:
            logger.debug(f"[MANUAL] Position open — spread={current:.2f}  entry={position['entry_premium']:.2f}")
        else:
            logger.debug("[MANUAL] Position open — no spread LTP this tick, spot check ran.")
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
        entry  = position["entry_premium"]
        qty    = position["qty"]
        pnl    = round((exit_premium - entry) * qty, 2)
        capital = self.risk.record_trade_close(pnl)

        import journal
        journal.log_trade_close(reason, exit_premium, pnl, capital)

        buy_sym  = _kite_symbol(position["buy_strike"],  position["option_type"], position["expiry"])
        sell_sym = _kite_symbol(position["sell_strike"], position["option_type"], position["expiry"])

        reason_labels = {
            "profit_target":     "PROFIT TARGET HIT",
            "stop_loss":         "STOP LOSS HIT",
            "spot_stop_adverse": "SPOT MOVE STOP HIT",
            "force_exit_3pm":    "3 PM FORCE EXIT",
        }
        label = reason_labels.get(reason, reason.upper())
        pnl_str = f"+Rs.{pnl:.2f}" if pnl >= 0 else f"Rs.{pnl:.2f}"

        alert = (
            f"EXIT NOW — {label}\n"
            f"\n"
            f"On Kite app close your spread:\n"
            f"1. SELL {buy_sym}  Qty:{qty}  MARKET\n"
            f"2. BUY  {sell_sym}  Qty:{qty}  MARKET\n"
            f"\n"
            f"Entry: Rs.{entry:.2f} | Now: Rs.{exit_premium:.2f}\n"
            f"P&L  : {pnl_str}\n"
            f"Capital after: Rs.{capital:.2f}"
        )
        logger.info(alert)
        send_alert(
            alert,
            title=f"EXIT NOW — {label}",
            priority="urgent" if pnl < 0 else "high",
        )
