"""
Builds the spread order parameters from a Signal.

Strike selection:
  Bull spread: Buy ATM CE, Sell (ATM + 100) CE
  Bear spread: Buy ATM PE, Sell (ATM - 100) PE
  ATM = Nifty spot rounded to nearest 50

Always trades exactly 1 lot (65 units). No half lots — Nifty does not allow it.

Net debit check: must be <= signal.max_premium (varies by VIX tier) — returns None if this fails.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class SpreadOrder:
    direction:     str    # 'bull' or 'bear'
    option_type:   str    # 'CE' or 'PE'
    buy_strike:    int
    sell_strike:   int
    expiry:        str    # e.g. '24-Mar-2026'
    entry_premium: float  # net debit per unit (buy_ltp - sell_ltp)
    qty:           int    # always LOT_SIZE (65)
    total_debit:   float  # entry_premium × qty (total cash outflow)


def build_spread(nse_client, signal) -> Optional[SpreadOrder]:
    atm    = _round_to_nearest_50(signal.spot)
    expiry = nse_client.get_weekly_expiry()

    if signal.direction == "bull":
        option_type = "CE"
        buy_strike  = atm
        sell_strike = atm + config.SPREAD_WIDTH
    else:
        option_type = "PE"
        buy_strike  = atm
        sell_strike = atm - config.SPREAD_WIDTH

    # In manual mode skip LTP fetch — NSE option chain API blocks cloud IPs.
    # User checks live price on Kite before placing and verifies net debit.
    if config.MANUAL_TRADING:
        logger.info(
            f"Spread: {signal.direction.upper()} {option_type} "
            f"Buy {buy_strike}  Sell {sell_strike}  Expiry: {expiry}  "
            f"MaxDebit: Rs.{signal.max_premium}/unit"
        )
        return SpreadOrder(
            direction=signal.direction,
            option_type=option_type,
            buy_strike=buy_strike,
            sell_strike=sell_strike,
            expiry=expiry,
            entry_premium=0.0,
            qty=config.LOT_SIZE,
            total_debit=0.0,
        )

    buy_ltp   = nse_client.get_option_ltp(buy_strike,  option_type, expiry)
    sell_ltp  = nse_client.get_option_ltp(sell_strike, option_type, expiry)
    net_debit = round(buy_ltp - sell_ltp, 2)

    logger.info(
        f"Spread: {signal.direction.upper()} {option_type} "
        f"Buy {buy_strike}@{buy_ltp:.2f}  Sell {sell_strike}@{sell_ltp:.2f}  "
        f"Net debit/unit Rs.{net_debit:.2f}  Expiry: {expiry}"
    )

    if net_debit > signal.max_premium:
        logger.warning(f"Net debit Rs.{net_debit:.2f} > limit Rs.{signal.max_premium} — skipping.")
        return None

    if net_debit <= 0:
        logger.warning(f"Net debit Rs.{net_debit:.2f} <= 0 — invalid spread, skipping.")
        return None

    qty         = config.LOT_SIZE
    total_debit = round(net_debit * qty, 2)

    return SpreadOrder(
        direction=signal.direction,
        option_type=option_type,
        buy_strike=buy_strike,
        sell_strike=sell_strike,
        expiry=expiry,
        entry_premium=net_debit,
        qty=qty,
        total_debit=total_debit,
    )


def _round_to_nearest_50(price: float) -> int:
    if price <= 0:
        raise ValueError(f"Invalid spot price: {price}. NSE data may be corrupt.")
    return int(round(price / 50) * 50)
