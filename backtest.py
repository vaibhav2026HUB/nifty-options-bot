"""
Single-day backtest engine.

Uses:
  - yfinance for Nifty 1-minute OHLCV (available up to 7 trading days back)
  - Black-Scholes for theoretical option pricing (VIX as implied vol proxy)
  - Same entry/exit rules as the live bot

NOTE: Results are THEORETICAL — actual option prices will differ due to
bid-ask spreads, liquidity, and smile effects. Use as directional validation,
not as exact P&L prediction.

Usage:
  python backtest.py              # last trading day
  python backtest.py 2026-03-20   # specific date (within last 7 trading days)
"""

import sys
import math
import csv
import os
from datetime import date, datetime, timedelta

import pytz
import yfinance as yf

import config
from events.event_calendar import is_event_day

IST = pytz.timezone("Asia/Kolkata")
RISK_FREE_RATE = 0.065   # India ~6.5% per annum


# ─── Black-Scholes (no external dependencies) ─────────────────────────────────

def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _bs_call(S, K, T, r, sigma):
    if T <= 1e-9:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)

def _bs_put(S, K, T, r, sigma):
    if T <= 1e-9:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

def _spread_value(spot, buy_strike, sell_strike, option_type, T, sigma):
    r = RISK_FREE_RATE
    if option_type == "CE":
        return _bs_call(spot, buy_strike, T, r, sigma) - _bs_call(spot, sell_strike, T, r, sigma)
    else:
        return _bs_put(spot, buy_strike, T, r, sigma) - _bs_put(spot, sell_strike, T, r, sigma)

def _time_to_expiry(current_dt, expiry_date):
    """Years remaining to expiry (from current IST datetime to market close on expiry date)."""
    expiry_close = IST.localize(
        datetime.combine(expiry_date, datetime.strptime("15:30", "%H:%M").time())
    )
    seconds = (expiry_close - current_dt).total_seconds()
    return max(seconds / (365.25 * 24 * 3600), 0.0)

def _round50(price):
    return int(round(price / 50) * 50)

def _last_trading_day():
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ─── Data fetching ────────────────────────────────────────────────────────────

def _fetch_nifty_1min(backtest_date):
    print("  Fetching Nifty 1-min data (Yahoo Finance ^NSEI)...")
    df = yf.Ticker("^NSEI").history(period="5d", interval="1m")
    if df.empty:
        raise ValueError("Yahoo Finance returned no data for ^NSEI.")
    df.index = df.index.tz_convert(IST)
    day = df[df.index.date == backtest_date].copy()
    if day.empty:
        raise ValueError(f"No 1-min data for {backtest_date}. Market closed or >7 days ago.")
    print(f"  Got {len(day)} candles.")
    return day, df

def _fetch_vix(backtest_date):
    print("  Fetching India VIX (Yahoo Finance ^INDIAVIX)...")
    try:
        df = yf.Ticker("^INDIAVIX").history(period="5d", interval="1d")
        if df.empty:
            raise ValueError("Empty")
        df.index = df.index.tz_localize(None)
        row = df[df.index.date == backtest_date]
        vix = float(row["Close"].iloc[0]) if not row.empty else float(df["Close"].iloc[-1])
        print(f"  VIX: {vix:.2f}")
        return vix
    except Exception as e:
        print(f"  VIX fetch failed ({e}). Defaulting to 14.0")
        return 14.0

def _find_expiry(backtest_date):
    """
    Find the nearest weekly expiry after backtest_date.
    Based on confirmed data: Mar 24 2026 is Tuesday expiry.
    We find the next Tuesday (weekday=1) after backtest_date.
    If NSE changes expiry day, update weekday here.
    """
    EXPIRY_WEEKDAY = 1   # Tuesday (confirmed from Kite Mar 2026)
    d = backtest_date + timedelta(days=1)
    while d.weekday() != EXPIRY_WEEKDAY:
        d += timedelta(days=1)
    return d


# ─── Main backtest ────────────────────────────────────────────────────────────

def run_backtest(backtest_date: date):
    print()
    print("=" * 60)
    print(f"  BACKTEST — {backtest_date.strftime('%A %d-%b-%Y')}")
    print(f"  Mode: {'PAPER' if config.PAPER_TRADING else 'LIVE'} config")
    print("=" * 60)

    # Weekend / event day check
    if backtest_date.weekday() >= 5:
        print("  Saturday/Sunday — no trading.")
        return
    if is_event_day(backtest_date):
        print("  Event day — no trading per calendar.")
        return

    # ── Fetch data ────────────────────────────────────────────────────────────
    try:
        day_df, full_df = _fetch_nifty_1min(backtest_date)
        vix              = _fetch_vix(backtest_date)
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    # ── Prev close ────────────────────────────────────────────────────────────
    prev_df = full_df[full_df.index.date < backtest_date]
    if prev_df.empty:
        print("  Cannot determine prev close — run again tomorrow for more history.")
        return
    prev_close = float(prev_df["Close"].iloc[-1])
    print(f"  Prev close : {prev_close:.2f}")

    # ── VIX check ─────────────────────────────────────────────────────────────
    if vix < config.VIX_MIN:
        print(f"  VIX {vix:.2f} < {config.VIX_MIN} — premium too thin, skipping.")
        return

    if vix > config.VIX_MAX:
        print(f"  VIX {vix:.2f} > {config.VIX_MAX} — too risky, skipping.")
        return

    qty = config.LOT_SIZE   # always 1 full lot (65 units)

    # ── Direction at 9:20 AM ──────────────────────────────────────────────────
    t920 = IST.localize(datetime.combine(backtest_date, datetime.strptime("09:20", "%H:%M").time()))
    candles_920 = day_df[day_df.index >= t920]
    if candles_920.empty:
        print("  No data at/after 9:20 AM.")
        return

    spot_920  = float(candles_920["Close"].iloc[0])
    direction = "bull" if spot_920 > prev_close else ("bear" if spot_920 < prev_close else None)

    if direction is None:
        print("  Spot == prev close at 9:20 AM — no directional bias, no trade.")
        return

    print(f"  Spot 9:20  : {spot_920:.2f}  Prev close: {prev_close:.2f}  => {direction.upper()}")
    print(f"  VIX        : {vix:.2f}  Size: 1 lot ({qty} units)")

    # ── Strike selection at 9:30 AM ───────────────────────────────────────────
    t930 = IST.localize(datetime.combine(backtest_date, datetime.strptime("09:30", "%H:%M").time()))
    candles_930 = day_df[day_df.index >= t930]
    if candles_930.empty:
        print("  No data at 9:30 AM.")
        return

    spot_930    = float(candles_930["Close"].iloc[0])
    atm         = _round50(spot_930)
    expiry_date = _find_expiry(backtest_date)
    sigma       = vix / 100

    if direction == "bull":
        option_type = "CE"
        buy_strike  = atm
        sell_strike = atm + config.SPREAD_WIDTH
    else:
        option_type = "PE"
        buy_strike  = atm
        sell_strike = atm - config.SPREAD_WIDTH

    T_entry       = _time_to_expiry(t930, expiry_date)
    entry_premium = _spread_value(spot_930, buy_strike, sell_strike, option_type, T_entry, sigma)

    print()
    print(f"  ENTRY at 9:30 AM")
    print(f"  Spot       : {spot_930:.2f}  ATM: {atm}")
    print(f"  Expiry     : {expiry_date}  ({expiry_date.strftime('%A')})")
    print(f"  Spread     : {direction.upper()} {option_type}  Buy {buy_strike} / Sell {sell_strike}")
    print(f"  Net debit  : Rs.{entry_premium:.2f}/unit  (Rs.{entry_premium*qty:.2f} total)")

    # Check 4: debit limit
    if entry_premium > config.MAX_ENTRY_DEBIT_PER_UNIT:
        print(f"  SKIPPED    : Debit Rs.{entry_premium:.2f} > limit Rs.{config.MAX_ENTRY_DEBIT_PER_UNIT}/unit")
        return
    if entry_premium <= 0:
        print(f"  SKIPPED    : Invalid spread (debit <= 0)")
        return

    profit_target = config.PROFIT_TARGET_MULTIPLIER * entry_premium
    stop_loss_lvl = config.STOP_LOSS_MULTIPLIER * entry_premium
    spot_stop_bull = spot_930 * (1 - config.SPOT_MOVE_STOP_PCT)
    spot_stop_bear = spot_930 * (1 + config.SPOT_MOVE_STOP_PCT)

    print(f"  Profit tgt : Rs.{profit_target:.2f}/unit  (2x)")
    print(f"  Stop loss  : Rs.{stop_loss_lvl:.2f}/unit  (0.5x)")
    if direction == "bull":
        print(f"  Spot stop  : {spot_stop_bull:.2f}  (0.5% down from entry)")
    else:
        print(f"  Spot stop  : {spot_stop_bear:.2f}  (0.5% up from entry)")

    # ── Simulate minute-by-minute exit monitoring ─────────────────────────────
    print()
    exit_reason  = None
    exit_premium = 0.0
    exit_time    = None
    exit_spot    = None
    max_spread   = entry_premium
    min_spread   = entry_premium

    for ts, row in candles_930.iloc[1:].iterrows():
        spot = float(row["Close"])
        T    = _time_to_expiry(ts, expiry_date)
        curr = _spread_value(spot, buy_strike, sell_strike, option_type, T, sigma)

        max_spread = max(max_spread, curr)
        min_spread = min(min_spread, curr)

        # Force exit at 3:00 PM
        if ts.hour >= 15:
            exit_reason, exit_premium, exit_time, exit_spot = "force_exit_3pm", curr, ts, spot
            break

        # Profit target
        if curr >= profit_target:
            exit_reason, exit_premium, exit_time, exit_spot = "profit_target", curr, ts, spot
            break

        # Stop loss
        if curr <= stop_loss_lvl:
            exit_reason, exit_premium, exit_time, exit_spot = "stop_loss", curr, ts, spot
            break

        # Spot move stop
        spot_move = (spot - spot_930) / spot_930
        if direction == "bull" and spot_move <= -config.SPOT_MOVE_STOP_PCT:
            exit_reason, exit_premium, exit_time, exit_spot = "spot_stop_adverse", curr, ts, spot
            break
        if direction == "bear" and spot_move >= config.SPOT_MOVE_STOP_PCT:
            exit_reason, exit_premium, exit_time, exit_spot = "spot_stop_adverse", curr, ts, spot
            break

    # If no exit triggered before market close
    if not exit_reason:
        last     = candles_930.iloc[-1]
        last_ts  = candles_930.index[-1]
        last_spot = float(last["Close"])
        T        = _time_to_expiry(last_ts, expiry_date)
        exit_premium = _spread_value(last_spot, buy_strike, sell_strike, option_type, T, sigma)
        exit_reason, exit_time, exit_spot = "market_close", last_ts, last_spot

    pnl = round((exit_premium - entry_premium) * qty, 2)

    # ── Results ───────────────────────────────────────────────────────────────
    print(f"  EXIT at {exit_time.strftime('%H:%M')}")
    print(f"  Reason     : {exit_reason.upper()}")
    print(f"  Spot       : {exit_spot:.2f}")
    print(f"  Exit prem  : Rs.{exit_premium:.2f}/unit")
    print()
    print(f"  Spread high: Rs.{max_spread:.2f}   Spread low: Rs.{min_spread:.2f}")
    print()
    if pnl >= 0:
        print(f"  P&L        : +Rs.{pnl:.2f}  WIN")
    else:
        print(f"  P&L        : Rs.{pnl:.2f}  LOSS")
    print(f"  Capital    : Rs.{config.INITIAL_CAPITAL} -> Rs.{config.INITIAL_CAPITAL + pnl:.2f}")
    print("=" * 60)
    print("  NOTE: Prices are theoretical (Black-Scholes + VIX). Actual")
    print("  option LTPs will differ. Use as direction check, not exact P&L.")
    print("=" * 60)

    # ── Log to backtest CSV ───────────────────────────────────────────────────
    os.makedirs("logs", exist_ok=True)
    log_path    = "logs/backtest.csv"
    write_header = not os.path.exists(log_path)
    mkt = "low_vix_full_size" if vix <= config.VIX_FULL_SIZE_MAX else "mid_vix_half_size"

    with open(log_path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["date", "index", "direction", "vix", "buy_strike", "sell_strike",
                        "entry_premium", "exit_premium", "exit_reason", "pnl",
                        "market_condition", "notes"])
        w.writerow([
            backtest_date, "NIFTY", direction, round(vix, 2),
            buy_strike, sell_strike,
            round(entry_premium, 2), round(exit_premium, 2),
            exit_reason, pnl, mkt,
            f"qty={qty} spot_930={spot_930:.2f} expiry={expiry_date}"
        ])
    print(f"  Logged to {log_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            target = date.fromisoformat(sys.argv[1])
        except ValueError:
            print("Usage: python backtest.py 2026-03-20")
            sys.exit(1)
    else:
        target = _last_trading_day()

    run_backtest(target)
