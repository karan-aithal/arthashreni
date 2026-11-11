"""Allocation, concentration, look-through and overlap (F1). Weights are fractions of the valued part
of the portfolio; holdings with no usable value are excluded and the result is marked `estimated`."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal

from arthashreni.analytics import PRECISION
from arthashreni.analytics.valuation import HoldingValue, split_valued
from arthashreni.dq.measured import Measured, QualityStatus, m_div, m_mul, m_sum, missing, with_status, worst

Dimension = Literal["asset_class", "country", "currency", "sector", "market_cap", "holding"]
UNKNOWN = "UNKNOWN"

_KEYS: dict[Dimension, Callable[[HoldingValue], str]] = {
    "asset_class": lambda h: h.position.asset.asset_class,
    "country": lambda h: h.position.asset.country,
    "currency": lambda h: h.position.asset.currency,
    "sector": lambda h: h.position.asset.sector or UNKNOWN,
    "market_cap": lambda h: h.position.asset.market_cap or UNKNOWN,
    "holding": lambda h: h.position.asset.name,
}


@dataclass(frozen=True)
class Slice:
    key: str
    value_inr: Measured[Decimal]
    weight: Measured[Decimal]


def allocate(holdings: Sequence[HoldingValue], by: Dimension) -> list[Slice]:
    """Weights by one dimension, largest first. They add up to 1 over the valued holdings. Holdings
    held in several accounts count once per key, so 'holding' is per asset, not per account."""
    valued, unvalued = split_valued(holdings)
    if not valued:
        return []
    groups: dict[str, list[Measured[Decimal]]] = {}
    for h in valued:
        groups.setdefault(_KEYS[by](h), []).append(h.value_inr)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        total = m_sum(v for vs in groups.values() for v in vs)
        if unvalued:
            note = (
                f"{len(unvalued)} of {len(holdings)} holdings have no usable value; "
                "weights are shares of the valued part"
            )
            total = with_status(total, QualityStatus.ESTIMATED, note)
        slices = []
        for key, values in groups.items():
            value = m_sum(values)
            slices.append(Slice(key, value, m_div(value, total)))
    return sorted(slices, key=lambda s: (-(s.value_inr.value or 0), s.key))


@dataclass(frozen=True)
class Concentration:
    top_n: int
    top_n_weight: Measured[Decimal]
    hhi: Measured[Decimal]  # sum of squared weights: 1/N when equal, 1 when one holding


def concentration(holdings: Sequence[HoldingValue], n: int = 3) -> Concentration:
    """Top-N weight and Herfindahl-Hirschman index over single holdings."""
    slices = allocate(holdings, "holding")
    if not slices:
        gone: Measured[Decimal] = missing(note="no valued holdings")
        return Concentration(n, gone, gone)
    weights = [s.weight for s in slices]
    status = worst(*(w.status for w in weights))
    as_of = min(w.as_of for w in weights)
    sources = tuple(dict.fromkeys(s for w in weights for s in w.source_ids))
    notes = tuple(dict.fromkeys(x for w in weights for x in w.notes))
    vals = [w.value for w in weights if w.value is not None]
    if len(vals) != len(weights):
        gone = Measured[Decimal](None, QualityStatus.MISSING, as_of, sources, (*notes, "a weight is missing"))
        return Concentration(n, gone, gone)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        top = sum(sorted(vals, reverse=True)[:n], Decimal(0))
        hhi = sum((v * v for v in vals), Decimal(0))
    return Concentration(
        n, Measured(top, status, as_of, sources, notes), Measured(hhi, status, as_of, sources, notes)
    )


# ---- look-through and overlap -------------------------------------------------------------------

FundHoldings = Mapping[int, Mapping[str, Decimal]]  # fund asset id -> {underlying key: weight in the fund}


@dataclass(frozen=True)
class LookThrough:
    exposure: dict[str, Measured[Decimal]]  # underlying key -> share of the whole portfolio
    without_data: tuple[str, ...]  # funds/ETFs whose holdings are not known


def look_through(
    holdings: Sequence[HoldingValue],
    fund_holdings: FundHoldings,
    fund_classes: frozenset[str] = frozenset({"MF", "ETF_IN", "ETF_US", "GOLD_ETF", "SILVER_ETF"}),
) -> LookThrough:
    """Exposure to underlying securities through funds, using their disclosed holdings where known.

    A fund with no holdings data is listed in `without_data`, never guessed; direct holdings count as
    themselves. Each exposure is `estimated` if any fund was missing data (shares are then of the whole
    portfolio but understate what the unknown funds hold).
    """
    valued, _ = split_valued(holdings)
    if not valued:
        return LookThrough({}, ())
    total = m_sum(h.value_inr for h in valued)
    exposure: dict[str, list[Measured[Decimal]]] = {}
    unknown: list[str] = []
    for h in valued:
        a = h.position.asset
        if a.asset_class in fund_classes:
            parts = fund_holdings.get(a.id)
            if not parts:
                unknown.append(a.name)
                continue
            for key, w in parts.items():
                weight = Measured(w, QualityStatus.OK, h.value_inr.as_of, ("fund_holdings",))
                exposure.setdefault(key, []).append(m_div(m_mul(h.value_inr, weight), total))
        else:
            exposure.setdefault(a.name, []).append(m_div(h.value_inr, total))
    out: dict[str, Measured[Decimal]] = {}
    for key, parts_m in exposure.items():
        m = m_sum(parts_m)
        out[key] = (
            with_status(m, QualityStatus.ESTIMATED, f"no holdings data for: {', '.join(unknown)}")
            if unknown
            else m
        )
    return LookThrough(out, tuple(unknown))


def overlap(
    a: Mapping[str, Decimal] | None, b: Mapping[str, Decimal] | None, as_of: datetime
) -> Measured[Decimal]:
    """Overlap of two funds: the sum of the smaller weight of every common underlying (0 = none, 1 = same)."""
    if not a or not b:
        return missing(as_of, "holdings data missing for one of the funds")
    return Measured(
        sum((min(w, b[k]) for k, w in a.items() if k in b), Decimal(0)),
        QualityStatus.OK,
        as_of,
        ("fund_holdings",),
    )
