"""
Trading journal analyser.

Reads logs/journal.csv and produces a full performance report:
  - Trade summary (win rate, avg P&L, best/worst)
  - No-trade breakdown (why days were skipped)
  - VIX regime performance
  - Exit reason breakdown
  - Capital curve
  - Leakage analysis (valid signals blocked by parameters)
  - Improvement suggestions

Usage:
  python analyse.py
"""

import csv
import os
from datetime import datetime
from collections import defaultdict

JOURNAL_PATH  = "logs/journal.csv"
BACKTEST_PATH = "logs/backtest.csv"


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="") as f:
        return list(csv.DictReader(f))


def _f(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _pct(num, denom):
    return f"{100 * num / denom:.1f}%" if denom else "N/A"


def run_analysis(include_backtest=True):
    rows = _load(JOURNAL_PATH)
    bt   = _load(BACKTEST_PATH) if include_backtest else []

    # Merge live journal + backtest rows for unified analysis
    all_rows = rows + bt
    if not all_rows:
        print("No journal data yet. Run the bot for a few days first.")
        return

    trades     = [r for r in all_rows if r.get("trade_taken") == "yes"
                  or r.get("exit_reason") not in ("", "-", None)]
    no_trades  = [r for r in all_rows if r.get("trade_taken") == "no"]
    total_days = len(all_rows)

    print()
    print("=" * 62)
    print("  NIFTY OPTIONS BOT — PERFORMANCE JOURNAL")
    print("=" * 62)
    print(f"  Total days analysed : {total_days}")
    print(f"  Days traded         : {len(trades)}")
    print(f"  Days skipped        : {len(no_trades)}")
    print(f"  Trade rate          : {_pct(len(trades), total_days)}")

    # ── P&L summary ───────────────────────────────────────────────────────────
    if trades:
        pnls    = [_f(r.get("pnl")) for r in trades]
        wins    = [p for p in pnls if p > 0]
        losses  = [p for p in pnls if p < 0]
        flat    = [p for p in pnls if p == 0]
        total   = sum(pnls)

        print()
        print("  P&L SUMMARY")
        print("  " + "-" * 40)
        print(f"  Total P&L           : Rs.{total:+.2f}")
        print(f"  Wins / Losses / Flat: {len(wins)} / {len(losses)} / {len(flat)}")
        print(f"  Win rate            : {_pct(len(wins), len(trades))}")
        print(f"  Avg P&L per trade   : Rs.{total/len(trades):+.2f}")
        if wins:
            print(f"  Avg win             : Rs.{sum(wins)/len(wins):+.2f}")
            print(f"  Best trade          : Rs.{max(wins):+.2f}")
        if losses:
            print(f"  Avg loss            : Rs.{sum(losses)/len(losses):+.2f}")
            print(f"  Worst trade         : Rs.{min(losses):+.2f}")
        if wins and losses:
            rr = abs(sum(wins)/len(wins)) / abs(sum(losses)/len(losses))
            print(f"  Reward/Risk ratio   : {rr:.2f}x")

    # ── No-trade breakdown ────────────────────────────────────────────────────
    if no_trades:
        print()
        print("  WHY WE SKIPPED (no-trade breakdown)")
        print("  " + "-" * 40)
        reasons = defaultdict(int)
        for r in no_trades:
            reasons[r.get("no_trade_reason", "unknown")] += 1
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            label = {
                "vix_too_high":       "VIX > 20 (too volatile)",
                "event_day":          "Event day (RBI/FOMC/Budget)",
                "no_direction":       "Spot = prev close (no bias)",
                "debit_too_high":     "Net debit > Rs.100/unit",
                "daily_loss_limit":   "Daily loss limit hit",
                "weekly_floor":       "Weekly capital floor hit",
                "already_traded":     "Already traded today",
                "started_late":       "Bot started after 9:20 AM",
                "prev_close_missing": "Prev close unavailable",
                "mid_vix_half_size":  "VIX 16-20 (half size traded)",
            }.get(reason, reason)
            print(f"  {label:<35} {count:>3} day(s)  {_pct(count, total_days)}")

    # ── VIX regime performance ─────────────────────────────────────────────────
    if trades:
        print()
        print("  VIX REGIME PERFORMANCE")
        print("  " + "-" * 40)
        regimes = defaultdict(list)
        for r in trades:
            regimes[r.get("vix_regime", "-")].append(_f(r.get("pnl")))
        for regime in ["too_low", "tradeable", "too_high"]:
            ps = regimes.get(regime, [])
            if ps:
                wins_r = len([p for p in ps if p > 0])
                label  = {"too_low":    "VIX < 11  (thin premium — should skip)",
                           "tradeable":  "VIX 11-16 (trade zone — 1 full lot)",
                           "too_high":   "VIX > 16  (too risky — should skip)"}.get(regime)
                print(f"  {label:<40} {len(ps)} trade(s)  "
                      f"WR={_pct(wins_r, len(ps))}  "
                      f"Avg Rs.{sum(ps)/len(ps):+.2f}")

    # ── Exit reason breakdown ──────────────────────────────────────────────────
    if trades:
        print()
        print("  EXIT REASON BREAKDOWN")
        print("  " + "-" * 40)
        exits = defaultdict(list)
        for r in trades:
            exits[r.get("exit_reason", "-")].append(_f(r.get("pnl")))
        labels = {
            "profit_target":    "Profit target hit (2x)",
            "stop_loss":        "Stop loss hit (0.5x)",
            "spot_stop_adverse":"Spot move stop (0.5%)",
            "force_exit_3pm":   "Force exit 3 PM",
            "market_close":     "Market close (backtest)",
            "-":                "Position open / unknown",
        }
        for reason, ps in sorted(exits.items(), key=lambda x: -len(x[1])):
            if ps:
                wins_e = len([p for p in ps if p > 0])
                print(f"  {labels.get(reason, reason):<35} "
                      f"{len(ps)} trade(s)  "
                      f"WR={_pct(wins_e, len(ps))}  "
                      f"Avg Rs.{sum(ps)/len(ps):+.2f}")

    # ── Direction performance ──────────────────────────────────────────────────
    if trades:
        print()
        print("  DIRECTION PERFORMANCE")
        print("  " + "-" * 40)
        dirs = defaultdict(list)
        for r in trades:
            dirs[r.get("direction", "-")].append(_f(r.get("pnl")))
        for d, ps in dirs.items():
            wins_d = len([p for p in ps if p > 0])
            print(f"  {d.upper():<10}  {len(ps)} trade(s)  "
                  f"WR={_pct(wins_d, len(ps))}  "
                  f"Avg Rs.{sum(ps)/len(ps):+.2f}")

    # ── Capital curve ──────────────────────────────────────────────────────────
    if trades:
        print()
        print("  CAPITAL CURVE")
        print("  " + "-" * 40)
        dated = []
        for r in trades:
            try:
                d = r.get("date", "")
                p = _f(r.get("pnl"))
                c = _f(r.get("capital_after"))
                if d and d != "-":
                    dated.append((d, p, c))
            except Exception:
                pass
        dated.sort(key=lambda x: x[0])
        for d, p, c in dated:
            bar = ("+" if p >= 0 else "-") * min(int(abs(p) / 200), 20)
            print(f"  {d}  Rs.{p:+7.2f}  |{bar:<20}  Capital: Rs.{c:.2f}")

    # ── Leakage analysis ───────────────────────────────────────────────────────
    print()
    print("  LEAKAGE ANALYSIS")
    print("  " + "-" * 40)
    high_vix_days = [r for r in no_trades if r.get("no_trade_reason") == "vix_too_high"]
    debit_days    = [r for r in no_trades if r.get("no_trade_reason") == "debit_too_high"]

    if high_vix_days:
        vix_vals = [_f(r.get("vix")) for r in high_vix_days if _f(r.get("vix")) > 0]
        if vix_vals:
            print(f"  High VIX skips: {len(high_vix_days)} days  "
                  f"Avg VIX={sum(vix_vals)/len(vix_vals):.1f}  "
                  f"Max VIX={max(vix_vals):.1f}")
            print(f"  -> If VIX limit raised to 22, these may have traded.")
    if debit_days:
        print(f"  Debit-too-high skips: {len(debit_days)} days")
        print(f"  -> Consider raising MAX_ENTRY_DEBIT_PER_UNIT from Rs.100.")
    if not high_vix_days and not debit_days:
        print("  No parameter-driven leakage detected yet.")

    # ── Suggestions ───────────────────────────────────────────────────────────
    print()
    print("  IMPROVEMENT SIGNALS (data-driven)")
    print("  " + "-" * 40)
    suggestions = []

    if trades:
        pnls = [_f(r.get("pnl")) for r in trades]
        # Losing >40% of trades
        loss_rate = len([p for p in pnls if p < 0]) / len(pnls) if pnls else 0
        if loss_rate > 0.4:
            suggestions.append("Win rate <60%: review entry filters or reduce debit limit.")

        # Force exit dominating
        force_exits = exits.get("force_exit_3pm", [])
        if len(force_exits) > len(trades) * 0.5:
            avg_fe = sum(force_exits) / len(force_exits) if force_exits else 0
            if avg_fe < 0:
                suggestions.append("Force exits are net negative: consider earlier stop or trailing exit.")
            else:
                suggestions.append("Holding to 3 PM is profitable: current exit timing looks good.")

        # Stop loss dominating
        stops = exits.get("stop_loss", []) + exits.get("spot_stop_adverse", [])
        if len(stops) > len(trades) * 0.4:
            suggestions.append("High stop-out rate: consider wider stop (0.4x) or smaller position.")

    if not suggestions:
        suggestions.append("Not enough data yet — need 10+ trading days for reliable signals.")

    for i, s in enumerate(suggestions, 1):
        print(f"  {i}. {s}")

    print()
    print("=" * 62)
    print(f"  Run again after more trading days for stronger signals.")
    print("=" * 62)
    print()


if __name__ == "__main__":
    run_analysis()
