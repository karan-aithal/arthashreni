"""Source-specific calendars and expected-date logic (E3 staleness, ADR-0023).

Each source has the calendar of *its own* publication days, never a borrowed one:

- `XNYS`: NYSE sessions (`exchange_calendars`), for US prices.
- `XBOM`: BSE sessions, only for prices of BSE/NSE-listed instruments (exchange-traded ETFs and stocks).
- `AMFI`: days on which AMFI published NAVs, derived from our own NAV history.
- `FBIL`: days on which FBIL published a reference rate, derived from our own FBIL history.
- `SBI`: days on which SBI's archive has a row, derived from our own archive history.
- `WEEKDAY`: Monday to Friday, the fallback when nothing better is known.

A derived calendar needs no holiday list: inside the span of dates we have observed, a day is a session
only if it was observed (gaps are the source's holidays or missed publications); outside that span, and
whenever there is no history, weekdays are assumed. `expected_latest` reports which case applied through
`calendar_is_assumed`, and a stale verdict says so in its note.
"""

from collections.abc import Collection
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Literal

import exchange_calendars as xc
from exchange_calendars.errors import DateOutOfBounds

Market = Literal["XNYS", "XBOM", "AMFI", "FBIL", "SBI", "WEEKDAY"]
DERIVED: frozenset[str] = frozenset({"AMFI", "FBIL", "SBI"})


@lru_cache
def _cal(code: str) -> Any:
    return xc.get_calendar(code)


def _weekday(d: date) -> bool:
    return d.weekday() < 5


def calendar_is_assumed(market: Market, observed: Collection[date]) -> bool:
    """True when a derived calendar has no history yet, so weekdays are only an assumption."""
    return market in DERIVED and not observed


def is_session(market: Market, d: date, observed: Collection[date] = ()) -> bool:
    """True if the source publishes on `d`. `observed` is the history that derived calendars rely on."""
    if market in DERIVED:
        if not observed:
            return _weekday(d)
        if min(observed) <= d <= max(observed):
            return d in observed
        return _weekday(d)
    if market == "WEEKDAY":
        return _weekday(d)
    try:
        return bool(_cal(market).is_session(d.isoformat()))
    except DateOutOfBounds:  # beyond the packaged calendar: assume weekdays, never crash
        return _weekday(d)


def last_session_on_or_before(market: Market, d: date, observed: Collection[date] = ()) -> date:
    for back in range(0, 15):  # no source is shut for 15 days running
        cur = d - timedelta(days=back)
        if is_session(market, cur, observed):
            return cur
    return d - timedelta(days=14)


def expected_latest(market: Market, today: date, delay_days: int, observed: Collection[date] = ()) -> date:
    """Newest session a healthy source should already have published by `today`."""
    return last_session_on_or_before(market, today - timedelta(days=delay_days), observed)
