# Nifty Options Bot

An autonomous trading bot that executes Nifty 50 options spreads on real capital. Built in Python, runs on a daily schedule via GitHub Actions.

The most interesting thing about it: **it rejects the trade more often than it takes it.** Skipping is the product.

---

## What it does

Every trading day at 9:20 AM IST, the bot wakes up and runs a decision pipeline:

1. **VIX regime check** — if VIX > 20, no trade. Too fearful. If VIX < 11, no trade. Premium too thin.
2. **Event calendar check** — FOMC days, expiry weeks, RBI policy days → skip.
3. **Day filter** — Fridays skipped. Expiry day skipped.
4. **Directional signal** — compares Nifty spot at 9:20 vs previous close. Bull or bear bias.
5. **Spread construction** — builds a bull call spread (CE) or bear put spread (PE) with defined max debit.
6. **Position sizing** — lot multiplier scales with capital tier.

If any gate fails → log the reason, send an alert, shut down for the day. No override.

At 3:00 PM IST — force exit any open position. No carrying overnight.

---

## Trade journal (live)

Real entries from `logs/journal.csv`:

| Date | Direction | VIX | Result |
|------|-----------|-----|--------|
| 2026-03-17 | Bull CE spread | 19.79 | +₹490 |
| 2026-03-18 | — | — | Skipped: FOMC event day |
| 2026-03-19 | — | 22.8 | Skipped: VIX too high |
| 2026-03-20 | — | 22.81 | Skipped: VIX too high |
| 2026-04-08 | — | 19.9 | Skipped: VIX borderline |
| 2026-05-26 | — | — | Skipped: expiry day |
| 2026-05-29 | — | — | Skipped: Friday |

More skips than trades. That's by design.

---

## Architecture

```
main.py               — scheduler + entry/exit orchestration
strategy/
  signal.py           — VIX regime, directional bias, day filters
  spread_builder.py   — strike selection, debit validation
risk/
  risk_manager.py     — position sizing, daily loss limits, state
execution/
  upstox_trader.py    — live order execution via Upstox API
  paper_trader.py     — paper trading mode (default)
  manual_trader.py    — alert-only mode (no auto execution)
alerts/
  notifier.py         — email + Telegram alerts
data/
  nse_client.py       — NSE option chain + prev close fetch
events/
  event_calendar.py   — hardcoded + dynamic no-trade calendar
journal.py            — CSV trade journal writer
```

---

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your credentials
python main.py
```

Default mode is **paper trading** — safe to run immediately. Set `PAPER_TRADING=False` in `.env` only after 2+ weeks of verified paper results.

Bot is designed to be started before 9:20 AM IST. If started between 9:20–11:00 AM, catch-up mode fires entry immediately.

---

## Environment variables

See `.env.example`. Required:

- `UPSTOX_API_KEY` / `UPSTOX_API_SECRET` — from developer.upstox.com
- `UPSTOX_MOBILE` / `UPSTOX_PIN` / `UPSTOX_TOTP_SECRET` — for headless OAuth via Playwright
- `ALERT_EMAIL_*` / `TELEGRAM_*` — for trade alerts (optional but recommended)

Never commit `.env`. It is gitignored.

---

## CI / scheduled runs

`.github/workflows/bot.yml` runs the bot on a cron schedule (IST) via GitHub Actions. Secrets are stored as GitHub repository secrets — never in code.

---

## Philosophy

Options trading rewards patience, not frequency. This bot is built around one constraint: **only trade when conditions are genuinely clean.** A system that sits out bad days is more valuable than one that finds reasons to trade every day.

Capital starts at ₹32,500. Position size scales with capital. The goal is not to win big — it's to not lose stupidly.
