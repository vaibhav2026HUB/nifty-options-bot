"""
Trading journal — logs every day the bot runs, whether a trade was taken or not.

Every row = one trading day attempt. This gives a complete picture of:
  - Days traded vs skipped and why
  - VIX regime distribution
  - Exit reason breakdown
  - Capital curve
  - Leakage: days with valid signals that were blocked by parameters

Log file: logs/journal.csv
"""

import csv
import os
import logging
from datetime import date, datetime

import pytz
import config

IST    = pytz.timezone("Asia/Kolkata")
logger = logging.getLogger(__name__)

JOURNAL_PATH = "logs/journal.csv"

HEADERS = [
    "date",
    "weekday",
    "trade_taken",       # yes / no
    "no_trade_reason",   # vix_too_high | event_day | no_direction | debit_too_high | daily_loss_limit | weekly_floor | already_traded | started_late
    "vix",
    "vix_regime",        # low (<16) | mid (16-20) | high (>20)
    "prev_close",
    "spot_920",
    "direction",         # bull | bear | -
    "atm_strike",
    "buy_strike",
    "sell_strike",
    "option_type",       # CE | PE | -
    "expiry",
    "lot_size",
    "entry_premium",
    "total_debit",
    "profit_target",     # per unit
    "stop_loss_lvl",     # per unit
    "exit_time",
    "exit_reason",       # profit_target | stop_loss | spot_stop_adverse | force_exit_3pm | -
    "exit_premium",
    "pnl",
    "capital_before",
    "capital_after",
    "notes",
]


def _ensure_file():
    os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
    if not os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, "w", newline="") as f:
            csv.writer(f).writerow(HEADERS)


def _vix_regime(vix):
    if vix <= 0:
        return "-"
    if vix < config.VIX_MIN:
        return "too_low"      # < 11: premium too thin
    if vix <= config.VIX_MAX:
        return "tradeable"    # 11-20: trade zone (tiered)
    return "too_high"         # > 20: skip


def log_no_trade(reason: str, vix: float = 0, prev_close: float = 0,
                 spot_920: float = 0, capital: float = 0, notes: str = ""):
    """Call this whenever the bot decides NOT to trade."""
    _ensure_file()
    today = date.today()
    row = {h: "-" for h in HEADERS}
    row.update({
        "date":           str(today),
        "weekday":        today.strftime("%A"),
        "trade_taken":    "no",
        "no_trade_reason": reason,
        "vix":            round(vix, 2) if vix else "-",
        "vix_regime":     _vix_regime(vix),
        "prev_close":     round(prev_close, 2) if prev_close else "-",
        "spot_920":       round(spot_920, 2) if spot_920 else "-",
        "capital_before": round(capital, 2) if capital else "-",
        "capital_after":  round(capital, 2) if capital else "-",
        "notes":          notes,
    })
    with open(JOURNAL_PATH, "a", newline="") as f:
        csv.writer(f).writerow([row[h] for h in HEADERS])
    logger.info(f"Journal: no trade — {reason}")


def log_trade_open(signal, spread_order, capital_before: float):
    """Call this when a trade is entered. Returns a journal_id (date string)."""
    _ensure_file()
    today = date.today()
    row = {h: "-" for h in HEADERS}
    row.update({
        "date":           str(today),
        "weekday":        today.strftime("%A"),
        "trade_taken":    "yes",
        "no_trade_reason": "-",
        "vix":            round(signal.vix, 2),
        "vix_regime":     _vix_regime(signal.vix),
        "prev_close":     round(signal.prev_close, 2),
        "spot_920":       round(signal.spot, 2),
        "direction":      spread_order.direction,
        "atm_strike":     spread_order.buy_strike,   # ATM = buy_strike for both spreads
        "buy_strike":     spread_order.buy_strike,
        "sell_strike":    spread_order.sell_strike,
        "option_type":    spread_order.option_type,
        "expiry":         spread_order.expiry,
        "lot_size":       spread_order.qty,
        "entry_premium":  round(spread_order.entry_premium, 2),
        "total_debit":    round(spread_order.total_debit, 2),
        "profit_target":  round(spread_order.entry_premium * config.PROFIT_TARGET_MULTIPLIER, 2),
        "stop_loss_lvl":  round(spread_order.entry_premium * config.STOP_LOSS_MULTIPLIER, 2),
        "capital_before": round(capital_before, 2),
        "notes":          f"qty={spread_order.qty}",
    })
    # Write partial row — will be updated on exit
    with open(JOURNAL_PATH, "a", newline="") as f:
        csv.writer(f).writerow([row[h] for h in HEADERS])
    logger.info(f"Journal: trade opened")
    return str(today)


def log_trade_close(exit_reason: str, exit_premium: float,
                    pnl: float, capital_after: float):
    """
    Updates the last row in the journal with exit details.
    Called when a position is closed.
    """
    _ensure_file()
    rows = []
    with open(JOURNAL_PATH, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        logger.warning("Journal: no rows to update on close.")
        return

    # Update the last row that has trade_taken=yes and no exit yet
    for row in reversed(rows):
        if row.get("trade_taken") == "yes" and row.get("exit_reason") == "-":
            now = datetime.now(IST)
            row["exit_time"]    = now.strftime("%H:%M")
            row["exit_reason"]  = exit_reason
            row["exit_premium"] = round(exit_premium, 2)
            row["pnl"]          = round(pnl, 2)
            row["capital_after"] = round(capital_after, 2)
            break

    with open(JOURNAL_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Journal: trade closed — {exit_reason} P&L Rs.{pnl:+.2f}")
