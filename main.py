"""
Nifty Options Trading Bot — Main Entry Point

Startup steps:
  1. Make sure .env file is filled in (copy from .env.example)
  2. Paper trading (PAPER_TRADING=True) is the default — safe to run immediately
  3. Run BEFORE 9:20 AM IST:  python main.py
     If you start after 9:20 AM, the bot will warn you and skip today's trade.

For live trading (after 2+ weeks of paper verification):
  1. Run: python auth.py  (before 9:20 AM each day)
  2. Set PAPER_TRADING=False in .env
  3. Run: python main.py

IMPORTANT: Your system clock must be set to IST (UTC+5:30), or the bot
will fire entry/exit jobs at wrong times. Verify with: date command.
"""

import logging
import os
import time
from datetime import datetime, date

import schedule
import pytz

import config
from data.nse_client import NSEClient
from auth import get_kite_client
from strategy.signal import get_signal
from strategy.spread_builder import build_spread
from risk.risk_manager import RiskManager
from alerts.notifier import send_alert
from alerts.telegram_bot import check_commands, send_telegram
from events.event_calendar import get_upcoming_events
import journal

# ─── Timezone ──────────────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

# ─── Logging setup (logs/ dir created BEFORE FileHandler) ─────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/bot.log"),
    ],
)
logger = logging.getLogger(__name__)

# ─── Global state ──────────────────────────────────────────────────────────────
nse_client    = None
trader        = None
risk_manager  = None
_signal_cache = None   # Signal computed at 9:20, consumed at 9:30
_prev_close   = None   # Fetched from NSE at startup


# ──────────────────────────────────────────────────────────────────────────────
# Scheduled jobs
# ──────────────────────────────────────────────────────────────────────────────

def job_entry_check():
    """9:20 AM IST — Run all entry checks and cache signal."""
    global _signal_cache

    if not _is_weekday():
        return

    logger.info("=" * 55)
    logger.info("ENTRY CHECK (9:20 AM IST)")
    logger.info("=" * 55)

    capital = risk_manager.get_capital()

    can_trade, reason = risk_manager.can_trade()
    if not can_trade:
        logger.info(f"Risk gate blocked: {reason}")
        send_alert(f"[BOT] No trade today — {reason}")
        journal.log_no_trade(reason, capital=capital)
        _signal_cache = None
        return

    if _prev_close is None:
        logger.error("CRITICAL: prev_close is None — cannot determine direction. Aborting.")
        journal.log_no_trade("prev_close_missing", capital=capital)
        _signal_cache = None
        return

    signal = get_signal(nse_client, _prev_close)

    if signal.skip_reason:
        logger.info(f"No trade — {signal.skip_reason}")
        send_alert(f"[BOT] No trade — {signal.skip_reason}")
        journal.log_no_trade(
            signal.skip_reason,
            vix=signal.vix, prev_close=signal.prev_close,
            spot_920=signal.spot, capital=capital
        )
        _signal_cache = None
    else:
        _signal_cache = signal
        logger.info(f"Signal cached: {signal.direction.upper()} | VIX={signal.vix:.1f}")


def job_entry_execute():
    """9:30 AM IST — Execute cached signal if still valid."""
    global _signal_cache

    if not _is_weekday() or _signal_cache is None:
        return

    now = datetime.now(IST)
    # Allow entry only strictly before 11:00 AM
    if now.hour > 10:
        logger.info("Past 11:00 AM cutoff — discarding signal, no entry today.")
        _signal_cache = None
        return

    logger.info("=" * 55)
    logger.info("ENTRY EXECUTE (9:30 AM IST)")
    logger.info("=" * 55)

    spread_order = build_spread(nse_client, _signal_cache)
    if not spread_order:
        logger.info("Spread build failed — net debit too high or data issue.")
        send_alert("[BOT] Spread rejected — net debit > Rs.100/unit or data unavailable.")
        journal.log_no_trade(
            "debit_too_high",
            vix=_signal_cache.vix, prev_close=_signal_cache.prev_close,
            spot_920=_signal_cache.spot, capital=risk_manager.get_capital(),
            notes="Spread built but net debit exceeded limit"
        )
        _signal_cache = None
        return

    capital_before = risk_manager.get_capital()
    success = trader.enter(spread_order, _signal_cache)
    if success:
        journal.log_trade_open(_signal_cache, spread_order, capital_before)
    else:
        logger.error("Entry execution failed.")

    _signal_cache = None


def job_exit_check():
    """Every 60 seconds — check exit conditions for any open position."""
    if not _is_weekday():
        return

    now = datetime.now(IST)
    feh, fem = config.FORCE_EXIT_HOUR, config.FORCE_EXIT_MINUTE
    market_open = (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < feh)
    eod_window  = (now.hour == feh and now.minute <= fem + 5)
    if not (market_open or eod_window):
        return

    if risk_manager.get_open_position() is None:
        return

    try:
        trader.check_exits()
    except Exception as e:
        logger.warning(f"Exit check skipped this cycle: {e}")


def job_eod_summary():
    """3:15 PM IST — log end-of-day summary."""
    state = risk_manager.load_state()
    msg = (
        f"[BOT] End-of-Day Summary — {date.today()}\n"
        f"Today P&L : Rs.{state.get('today_pnl', 0):+.2f}\n"
        f"Capital   : Rs.{state.get('capital', 0):.2f}"
    )
    logger.info(msg)
    send_alert(msg)


def job_shutdown():
    """3:30 PM IST — clean shutdown. Task Scheduler will restart tomorrow at 9:00 AM."""
    logger.info("3:30 PM shutdown. See you tomorrow.")
    send_alert("[BOT] Shutting down for the day. Back tomorrow at 9:00 AM IST.")
    raise SystemExit(0)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _handle_telegram_command(cmd: str):
    """Respond to /status and /exit commands sent via Telegram."""
    if cmd.startswith("/status"):
        pos = risk_manager.get_open_position()
        state = risk_manager.load_state()
        if pos:
            pnl = pos.get("unrealised_pnl", 0)
            send_telegram(
                f"<b>Status</b>\n"
                f"Position: {pos.get('direction','').upper()} spread open\n"
                f"Unrealised P&L: Rs.{pnl:+.2f}\n"
                f"Capital: Rs.{state['capital']:.2f}"
            )
        else:
            send_telegram(
                f"<b>Status</b>\nNo open position\nCapital: Rs.{state['capital']:.2f}"
            )

    elif cmd.startswith("/exit"):
        pos = risk_manager.get_open_position()
        if pos:
            send_telegram("Force exit triggered via Telegram...")
            trader.force_exit("telegram_command")
        else:
            send_telegram("No open position to exit.")


def _is_weekday() -> bool:
    return date.today().weekday() < 5   # Mon=0 … Fri=4


def _maybe_catchup():
    """
    If bot started between 9:20–11:00 AM IST, run entry check (and execute) immediately
    instead of skipping the day. Handles GitHub Actions runner delays gracefully.
    """
    now = datetime.now(IST)
    after_check   = now.hour > 9 or (now.hour == 9 and now.minute >= 20)
    before_cutoff = now.hour < 11   # config.LAST_ENTRY_TIME = 11:00

    if not after_check:
        return  # Started on time — schedule handles everything

    if not before_cutoff:
        logger.warning(f"Started at {now.strftime('%H:%M')} IST — past 11:00 AM cutoff. No trade today.")
        return

    logger.info(f"[CATCHUP] Started at {now.strftime('%H:%M')} IST — running entry check now.")
    job_entry_check()

    after_execute = now.hour >= 10 or (now.hour == 9 and now.minute >= 30)
    if after_execute:
        if _signal_cache is not None:
            logger.info("[CATCHUP] Signal valid — running entry execute in 30 seconds.")
            time.sleep(30)
            job_entry_execute()
        else:
            logger.info("[CATCHUP] No valid signal — skipping execute.")


def _setup_schedule():
    schedule.every().day.at(config.ENTRY_CHECK_TIME).do(job_entry_check)
    schedule.every().day.at(config.ENTRY_EXECUTE_TIME).do(job_entry_execute)
    schedule.every(config.EXIT_CHECK_INTERVAL).seconds.do(job_exit_check)
    schedule.every().day.at(config.EOD_SUMMARY_TIME).do(job_eod_summary)
    schedule.every().day.at(config.SHUTDOWN_TIME).do(job_shutdown)

    logger.info(
        f"Schedule: entry check={config.ENTRY_CHECK_TIME}  "
        f"execute={config.ENTRY_EXECUTE_TIME}  "
        f"exit poll=every {config.EXIT_CHECK_INTERVAL}s  "
        f"force_exit={config.FORCE_EXIT_TIME}  "
        f"EOD={config.EOD_SUMMARY_TIME}  shutdown={config.SHUTDOWN_TIME}  (all times IST)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    global nse_client, trader, risk_manager, _prev_close

    logger.info("=" * 55)
    if config.BROKER == "upstox":
        mode = "UPSTOX LIVE"
    elif config.MANUAL_TRADING:
        mode = "MANUAL (alerts only)"
    elif config.PAPER_TRADING:
        mode = "PAPER"
    else:
        mode = "KITE LIVE"
    logger.info(f"Nifty Options Bot — {mode} mode")
    logger.info("=" * 55)

    nse_client   = NSEClient()
    risk_manager = RiskManager()

    # ── Fetch previous day close from NSE automatically ───────────────────────
    try:
        _prev_close = nse_client.get_prev_close()
        logger.info(f"Previous close fetched: {_prev_close}")
    except Exception as e:
        logger.error(f"Could not fetch prev_close from NSE: {e}")
        logger.error("Bot cannot determine trade direction. Exiting.")
        raise SystemExit(1)
    # ─────────────────────────────────────────────────────────────────────────

    if config.BROKER == "upstox":
        from upstox_auth import get_upstox_token, check_ip_changed
        check_ip_changed()  # alerts + logs if IP has changed since last token save
        from execution.upstox_trader import UpstoxTrader
        token = get_upstox_token()
        if token:
            trader = UpstoxTrader(token, nse_client, risk_manager)
            logger.info("Upstox live trader ready.")
        else:
            logger.warning("Upstox auth failed — falling back to ManualTrader.")
            send_alert("[BOT] WARNING: Upstox auth failed — running in manual alert mode today.")
            from execution.manual_trader import ManualTrader
            trader = ManualTrader(nse_client, risk_manager)
    elif config.MANUAL_TRADING:
        from execution.manual_trader import ManualTrader
        trader = ManualTrader(nse_client, risk_manager)
        logger.info("Manual trader ready — alerts will be sent to your phone.")
    elif config.PAPER_TRADING:
        from execution.paper_trader import PaperTrader
        trader = PaperTrader(nse_client, risk_manager)
        logger.info("Paper trader ready.")
    else:
        from execution.live_trader import LiveTrader
        kite   = get_kite_client()
        trader = LiveTrader(kite, nse_client, risk_manager)
        logger.info("Kite live trader ready.")

    # Upcoming event days
    upcoming = get_upcoming_events(7)
    if upcoming:
        logger.info(f"Upcoming no-trade days this week: {[str(d) for d in upcoming]}")

    state = risk_manager.load_state()
    logger.info(f"Current capital: Rs.{state['capital']:.2f}")

    send_alert(
        f"[BOT] Started — {mode}\n"
        f"Prev close: {_prev_close}\n"
        f"Capital: Rs.{state['capital']:.2f}"
    )

    _setup_schedule()
    _maybe_catchup()

    logger.info("Running. Press Ctrl+C to stop.")
    _last_cmd_check = 0
    while True:
        schedule.run_pending()
        now_ts = time.time()
        if now_ts - _last_cmd_check >= 30:
            _last_cmd_check = now_ts
            for cmd in check_commands():
                _handle_telegram_command(cmd)
        time.sleep(1)


if __name__ == "__main__":
    main()
