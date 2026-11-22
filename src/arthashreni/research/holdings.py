"""Fund and ETF holdings files (look-through and overlap input, F1).

AMC disclosure layouts differ and their terms are unchecked (V35), so holdings arrive as one documented CSV
template: `isin,name,pct_to_nav`, one row per disclosed line, cash and other lines included. A file is
accepted whole or rejected whole: weights must add up to 100% within 1 point, so a truncated or
misread file cannot become a quietly wrong look-through (D3).
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from arthashreni.dq.measured import QualityStatus

HEADER = ("isin", "name", "pct_to_nav")
TOLERANCE_PCT = Decimal(1)
# SEBI asks AMCs to publish month-end portfolios within 10 days of month end [VERIFY, V35].
DISCLOSURE_LAG_DAYS = 10
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class HoldingsError(ValueError):
    """The whole file is rejected."""


@dataclass(frozen=True)
class HoldingRow:
    key: str  # ISIN, or "name:<normalised name>" for lines without one (cash, TREPS)
    name: str
    weight: Decimal  # fraction of the fund's NAV, 0..1


def _name_key(name: str) -> str:
    return "name:" + re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def parse_holdings_csv(text: str) -> tuple[HoldingRow, ...]:
    reader = csv.reader(io.StringIO(text.removeprefix("\ufeff")))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows or tuple(c.strip().lower() for c in rows[0]) != HEADER:
        raise HoldingsError(f"header must be exactly {','.join(HEADER)}")
    out: dict[str, HoldingRow] = {}
    total = Decimal(0)
    for n, r in enumerate(rows[1:], start=2):
        if len(r) != 3:
            raise HoldingsError(f"line {n}: expected 3 columns, got {len(r)}")
        isin, name, pct_s = (c.strip() for c in r)
        if not name:
            raise HoldingsError(f"line {n}: name is empty")
        if isin and not _ISIN.match(isin.upper()):
            raise HoldingsError(f"line {n}: {isin!r} is not an ISIN")
        try:
            pct = Decimal(pct_s)
        except InvalidOperation:
            raise HoldingsError(f"line {n}: {pct_s!r} is not a number") from None
        if not pct.is_finite() or pct < 0 or pct > 100:
            raise HoldingsError(f"line {n}: weight {pct_s} is outside 0..100")
        key = isin.upper() if isin else _name_key(name)
        if key in out:
            raise HoldingsError(f"line {n}: {key} appears twice")
        out[key] = HoldingRow(key, name, pct / 100)
        total += pct
    if not out:
        raise HoldingsError("no holdings rows")
    if abs(total - 100) > TOLERANCE_PCT:
        raise HoldingsError(f"weights add up to {total}%, not 100% (+/- {TOLERANCE_PCT})")
    return tuple(out.values())


def expected_disclosure(today: date) -> date:
    """The month end whose portfolio should already be published on `today`."""
    month_end = (today - timedelta(days=DISCLOSURE_LAG_DAYS)).replace(day=1) - timedelta(days=1)
    return month_end


def holdings_status(valid_at: date, today: date) -> QualityStatus:
    """OK when the file is for the latest month end that must be public by now, else STALE."""
    return QualityStatus.OK if valid_at >= expected_disclosure(today) else QualityStatus.STALE
