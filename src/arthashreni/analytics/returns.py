"""Return measures: absolute, CAGR, TWR (XIRR is in `xirr.py`). Each names itself and says why (F1)."""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from arthashreni.analytics import PRECISION
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp, m_div, missing, worst

ONE = Decimal(1)


@dataclass(frozen=True)
class ReturnMeasure:
    """A return with the measure's name and why it was chosen, so no bare percentage is shown."""

    measure: str
    why: str
    result: Measured[Decimal]


def absolute_return(gain: Measured[Decimal], invested: Measured[Decimal]) -> ReturnMeasure:
    """Total gain over money put in. Ignores timing: fine for 'how much', wrong for 'how fast'."""
    return ReturnMeasure(
        "absolute return",
        "total gain over total invested; ignores when the money went in, so not comparable across periods",
        m_div(gain, invested),
    )


def cagr(
    start_value: Measured[Decimal], end_value: Measured[Decimal], start: date, end: date
) -> ReturnMeasure:
    """Compound annual growth of a single lump sum (actual/365). Not valid with interim cash flows."""
    measure, why = "CAGR", "one lump sum held between two dates; with several contributions use XIRR"
    days = (end - start).days
    if days <= 0:
        return ReturnMeasure(
            measure, why, missing(day_stamp(end), "CAGR needs the end date after the start date")
        )
    ratio = m_div(end_value, start_value)
    if ratio.value is None:
        return ReturnMeasure(measure, why, ratio)
    if ratio.value < 0:
        return ReturnMeasure(measure, why, missing(ratio.as_of, "CAGR undefined for a negative ending value"))
    with localcontext() as ctx:
        ctx.prec = PRECISION
        growth = ratio.value ** (Decimal(365) / Decimal(days)) - 1
    notes = (*ratio.notes, "CAGR: actual/365")
    if days < 365:
        notes = (*notes, "period shorter than one year: annualised figure is an extrapolation")
    return ReturnMeasure(measure, why, Measured(growth, ratio.status, ratio.as_of, ratio.source_ids, notes))


@dataclass(frozen=True)
class ValuePoint:
    """Portfolio value at the end of `day`, after that day's external `flow` (money in positive)."""

    day: date
    value: Measured[Decimal]
    flow: Decimal


def twr(points: Sequence[ValuePoint]) -> ReturnMeasure:
    """Time-weighted return: chain-linked sub-period returns, so contribution timing has no effect.

    Sub-period (t-1, t]: (value_t - flow_t) / value_(t-1). A sub-period that starts from zero value is
    skipped (there is nothing to measure yet). Result is cumulative, not annualised.
    """
    measure = "TWR"
    why = "chain-linked sub-period returns; removes the effect of when money was added or taken out"
    pts = sorted(points, key=lambda p: p.day)
    if len(pts) < 2:
        return ReturnMeasure(measure, why, missing(note="TWR needs at least two valuation dates"))
    status = worst(*(p.value.status for p in pts))
    as_of = min(p.value.as_of for p in pts)
    sources = tuple(dict.fromkeys(s for p in pts for s in p.value.source_ids))
    notes = tuple(dict.fromkeys(n for p in pts for n in p.value.notes))[:3]

    def fail(msg: str) -> ReturnMeasure:
        out = status if status.severity >= QualityStatus.MISSING.severity else QualityStatus.MISSING
        return ReturnMeasure(measure, why, Measured(None, out, as_of, sources, (*notes, msg)))

    if any(p.value.value is None for p in pts):
        return fail("a valuation date has no value")
    growth, periods = ONE, 0
    for prev, cur in itertools.pairwise(pts):
        assert prev.value.value is not None and cur.value.value is not None
        if prev.value.value <= 0:
            continue
        growth *= (cur.value.value - cur.flow) / prev.value.value
        periods += 1
    if periods == 0:
        return fail("the portfolio had no value at the start of any sub-period")
    return ReturnMeasure(measure, why, Measured(growth - 1, status, as_of, sources, notes))
