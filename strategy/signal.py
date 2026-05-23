"""
Entry signal generator — runs all pre-trade checks and returns a Signal.

VIX tiers:
  VIX < 11       → SKIP  (premium too thin)
  VIX 11–16      → TRADE Mon/Wed/Thu | max_premium=100 | SL=50% | PT=100%
  VIX 16–18      → TRADE Mon/Thu     | max_premium=80  | SL=40% | PT=75%  | need 0.5% move
  VIX 18–20      → TRADE Mon only    | max_premium=70  | SL=35% | PT=65%  | need 0.8% move
  VIX > 20       → SKIP  (too fearful)

Day rules (always apply regardless of VIX):
  Tuesday        → SKIP  (weekly expiry day)
  Friday         → SKIP  (weekend gap risk)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import date

from events.event_calendar import is_event_day

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    direction:         Optional[str]   # 'bull', 'bear', or None
    vix:               float
    spot:              float
    prev_close:        float
    skip_reason:       Optional[str]   # populated when direction is None
    max_premium:       float = 100.0   # max net debit per unit allowed
    stop_loss_pct:     float = 0.50    # exit when spread <= stop_loss_pct * entry
    profit_target_pct: float = 1.00    # exit when spread >= (1 + profit_target_pct) * entry


_VIX_TIERS = [
    # (vix_max, max_premium, stop_loss_pct, profit_target_pct, min_move_pct, allowed_days)
    (16, 100, 0.50, 1.00, 0.0, {0, 2, 3}),   # 11–16: Mon/Wed/Thu
    (18,  80, 0.40, 0.75, 0.5, {0, 3}),       # 16–18: Mon/Thu
    (20,  70, 0.35, 0.65, 0.8, {0}),          # 18–20: Mon only
]


def get_signal(nse_client, prev_close: float) -> Signal:
    today = date.today()
    dow   = today.weekday()   # 0=Mon … 4=Fri

    # Check 1 — Tuesday (expiry) and Friday (weekend gap risk)
    if dow == 1:
        logger.info("Tuesday expiry day — no trade.")
        return Signal(None, 0.0, 0.0, prev_close, "expiry_day")
    if dow == 4:
        logger.info("Friday — weekend gap risk, no trade.")
        return Signal(None, 0.0, 0.0, prev_close, "friday_skip")

    # Check 2 — Event day
    if is_event_day(today):
        logger.info("Event day — no trade.")
        return Signal(None, 0.0, 0.0, prev_close, "event_day")

    # Check 3 — India VIX
    vix = nse_client.get_india_vix()

    if vix < 11:
        logger.info(f"VIX {vix:.2f} < 11 — premium too thin, skipping.")
        return Signal(None, vix, 0.0, prev_close, f"vix_too_low_{vix:.1f}")

    if vix > 20:
        logger.info(f"VIX {vix:.2f} > 20 — too fearful, skipping.")
        return Signal(None, vix, 0.0, prev_close, f"vix_too_high_{vix:.1f}")

    # Assign VIX tier
    tier = next((t for t in _VIX_TIERS if vix <= t[0]), None)
    if tier is None:
        return Signal(None, vix, 0.0, prev_close, f"vix_no_tier_{vix:.1f}")

    _, max_premium, stop_loss_pct, profit_target_pct, min_move_pct, allowed_days = tier

    # Check 4 — Day allowed for this tier
    if dow not in allowed_days:
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        logger.info(f"VIX {vix:.2f} tier: {day_names[dow]} not in allowed days {[day_names[d] for d in sorted(allowed_days)]} — skipping.")
        return Signal(None, vix, 0.0, prev_close, f"vix_{vix:.1f}_day_skip")

    # Check 5 — Direction + minimum move confirmation
    spot     = nse_client.get_nifty_spot()
    move_pct = abs((spot - prev_close) / prev_close * 100)

    if move_pct < min_move_pct:
        logger.info(f"Move {move_pct:.2f}% < required {min_move_pct}% for VIX {vix:.1f} — skipping.")
        return Signal(None, vix, spot, prev_close, f"move_too_small_{move_pct:.1f}pct")

    if spot == prev_close:
        logger.info("Spot == prev close — no directional bias, skipping.")
        return Signal(None, vix, spot, prev_close, "no_direction")

    direction = "bull" if spot > prev_close else "bear"

    logger.info(
        f"Signal: {direction.upper()} | VIX={vix:.2f} | Move={move_pct:.2f}% | "
        f"MaxPremium=Rs.{max_premium} | PT={(1+profit_target_pct):.2f}x | SL={stop_loss_pct:.2f}x"
    )

    return Signal(
        direction=direction,
        vix=vix,
        spot=spot,
        prev_close=prev_close,
        skip_reason=None,
        max_premium=max_premium,
        stop_loss_pct=stop_loss_pct,
        profit_target_pct=profit_target_pct,
    )
