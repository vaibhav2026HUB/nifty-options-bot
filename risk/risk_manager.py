"""
Risk gate — enforces all capital and loss limits.

Limits enforced:
  - One trade per day maximum
  - Daily loss limit: ₹4,000
  - Weekly capital floor: ₹14,000

State is persisted in logs/state.json so limits survive process restarts.
"""

import json
import logging
import os
from datetime import date, timedelta
from typing import Optional, Tuple

import config

logger = logging.getLogger(__name__)


class RiskManager:

    def __init__(self, state_file: str = config.STATE_FILE_PATH):
        self.state_file = state_file

    # ─── State I/O ────────────────────────────────────────────────────────────

    def load_state(self) -> dict:
        if not os.path.exists(self.state_file):
            return self._default_state()
        with open(self.state_file, "r") as f:
            return json.load(f)

    def save_state(self, state: dict):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _default_state(self) -> dict:
        today      = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday
        return {
            "capital":            config.INITIAL_CAPITAL,
            "week_start_capital": config.INITIAL_CAPITAL,
            "week_start_date":    str(week_start),
            "today_date":         str(today),
            "today_traded":       False,
            "today_pnl":          0.0,
            "open_position":      None,
        }

    # ─── Risk checks ──────────────────────────────────────────────────────────

    def can_trade(self) -> Tuple[bool, str]:
        """
        Returns (True, '') if a new trade is allowed.
        Returns (False, reason) if blocked by any risk rule.
        """
        state = self._refresh_daily_state()

        # Weekly capital floor
        if state["capital"] < config.WEEKLY_CAPITAL_FLOOR:
            return False, (
                f"capital ₹{state['capital']:.0f} below weekly floor "
                f"₹{config.WEEKLY_CAPITAL_FLOOR} — no trades this week"
            )

        # Daily loss limit
        daily_loss = abs(min(state["today_pnl"], 0.0))
        if daily_loss >= config.DAILY_LOSS_LIMIT:
            return False, (
                f"daily loss ₹{daily_loss:.0f} hit limit ₹{config.DAILY_LOSS_LIMIT}"
            )

        # One trade per day
        if state["today_traded"]:
            return False, "already traded today"

        return True, ""

    # ─── Position recording ───────────────────────────────────────────────────

    def record_trade_open(self, position: dict):
        state = self.load_state()
        state["open_position"] = position
        state["today_traded"]  = True
        self.save_state(state)
        logger.info("Position recorded as open.")

    def record_trade_close(self, pnl: float) -> float:
        """Updates capital and today_pnl. Returns new capital."""
        state = self.load_state()
        state["capital"]      = round(state["capital"] + pnl, 2)
        state["today_pnl"]    = round(state.get("today_pnl", 0.0) + pnl, 2)
        state["open_position"] = None
        self.save_state(state)
        logger.info(f"Position closed. P&L: ₹{pnl:+.2f} | Capital: ₹{state['capital']:.2f}")
        return state["capital"]

    def get_open_position(self) -> Optional[dict]:
        return self.load_state().get("open_position")

    def get_capital(self) -> float:
        return self.load_state()["capital"]

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _refresh_daily_state(self) -> dict:
        """Reset daily fields if it's a new calendar day."""
        state = self.load_state()
        today = str(date.today())

        if state.get("today_date") != today:
            state["today_date"]   = today
            state["today_traded"] = False
            state["today_pnl"]    = 0.0
            state["open_position"] = None

        # Reset week_start_capital on Monday
        week_start = str(date.today() - timedelta(days=date.today().weekday()))
        if state.get("week_start_date") != week_start:
            state["week_start_date"]    = week_start
            state["week_start_capital"] = state["capital"]

        self.save_state(state)
        return state
