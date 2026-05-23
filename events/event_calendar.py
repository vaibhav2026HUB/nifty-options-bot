"""
Hardcoded calendar of high-impact event days — no new trades on these dates.

IMPORTANT — VERIFY BEFORE GOING LIVE:
  NSE publishes the official holiday list each year at:
  https://www.nseindia.com/resources/exchange-communication-holidays

  RBI MPC exact dates: https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx
  FOMC exact dates:    https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

  Every date below marked [VERIFY] should be confirmed against those sources
  before running in live mode. Dates marked [CONFIRMED] are fixed calendar dates
  (national holidays, Good Friday etc.) that do not change year to year.

Last reviewed: 2026-03-20
"""

from datetime import date

NO_TRADE_DATES = {

    # ─── NSE Market Holidays 2026 ─────────────────────────────────────────────
    # Source: https://www.nseindia.com/resources/exchange-communication-holidays
    # Verify this list against NSE's official 2026 holiday circular.

    date(2026, 1, 26),   # Republic Day              [CONFIRMED — always Jan 26]
    date(2026, 3, 4),    # Holi                      [VERIFY against NSE list]
    date(2026, 4, 3),    # Good Friday               [CONFIRMED — Easter Apr 5 2026]
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti      [CONFIRMED — always Apr 14]
    date(2026, 5, 1),    # Maharashtra Day            [CONFIRMED — always May 1]
    date(2026, 10, 2),   # Gandhi Jayanti             [CONFIRMED — always Oct 2]
    date(2026, 10, 20),  # Diwali Laxmi Puja          [VERIFY — lunar date changes yearly]
    date(2026, 11, 25),  # Gurunanak Jayanti          [VERIFY — lunar date changes yearly]
    date(2026, 12, 25),  # Christmas                  [CONFIRMED — always Dec 25]

    # NOTE: Independence Day (Aug 15 2026) falls on a Saturday — markets already
    # closed. Not listed here. If NSE adds a special holiday, add it manually.

    # ─── Union Budget ─────────────────────────────────────────────────────────
    date(2026, 2, 1),    # Union Budget Day           [CONFIRMED — traditionally Feb 1]

    # ─── RBI MPC Policy Announcement Days 2026 ───────────────────────────────
    # These are the decision/announcement days (Day 3 of 3-day MPC meetings).
    # Approximate schedule — VERIFY at rbi.org.in once officially announced.
    date(2026, 2, 7),    # RBI MPC                   [VERIFY]
    date(2026, 4, 9),    # RBI MPC                   [VERIFY]
    date(2026, 6, 6),    # RBI MPC                   [VERIFY]
    date(2026, 8, 6),    # RBI MPC                   [VERIFY]
    date(2026, 10, 1),   # RBI MPC                   [VERIFY]
    date(2026, 12, 3),   # RBI MPC                   [VERIFY]

    # ─── FOMC Meeting Result Days 2026 ───────────────────────────────────────
    # US Fed rate decisions — Indian markets react sharply on these days.
    # Announcement is on Day 2 of each 2-day meeting.
    # VERIFY at federalreserve.gov/monetarypolicy/fomccalendars.htm
    date(2026, 1, 28),   # FOMC                      [VERIFY]
    date(2026, 3, 18),   # FOMC                      [VERIFY]
    date(2026, 4, 29),   # FOMC                      [VERIFY]
    date(2026, 6, 10),   # FOMC                      [VERIFY]
    date(2026, 7, 29),   # FOMC                      [VERIFY]
    date(2026, 9, 16),   # FOMC                      [VERIFY]
    date(2026, 10, 28),  # FOMC                      [VERIFY]
    date(2026, 12, 9),   # FOMC                      [VERIFY]
}


def is_event_day(check_date: date = None) -> bool:
    """Returns True if the given date is a no-trade day."""
    if check_date is None:
        check_date = date.today()
    return check_date in NO_TRADE_DATES


def get_upcoming_events(n_days: int = 7) -> list:
    """Returns list of event dates within the next n_days (for alerts)."""
    from datetime import timedelta
    today = date.today()
    return [
        today + timedelta(days=i)
        for i in range(1, n_days + 1)
        if (today + timedelta(days=i)) in NO_TRADE_DATES
    ]
