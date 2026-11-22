"""Fundamental ratios from stored statement values (SEC `companyfacts`, US stocks only).

Every input and output is `Measured`; a missing or non-positive denominator gives `missing`."""

from datetime import datetime
from decimal import Decimal

from arthashreni.dq.measured import Measured, m_div, m_sub, missing


def _positive(m: Measured[Decimal]) -> bool:
    return m.value is not None and m.value > 0


def growth(current: Measured[Decimal], prior: Measured[Decimal], as_of: datetime) -> Measured[Decimal]:
    """(current - prior) / prior, for periods one year apart."""
    if not _positive(prior):
        return missing(as_of, "prior-year value missing or not positive")
    return m_div(m_sub(current, prior), prior)


def margin(profit: Measured[Decimal], revenue: Measured[Decimal], as_of: datetime) -> Measured[Decimal]:
    if not _positive(revenue):
        return missing(as_of, "revenue missing or not positive")
    return m_div(profit, revenue)
