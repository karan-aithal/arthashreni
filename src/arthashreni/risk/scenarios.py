"""Stress scenarios (F2). Labelled scenarios, never predictions: each applies fixed, illustrative shocks to the
current holdings and reports the INR impact per holding and in total. A mutual fund is treated as equity-oriented
unless its category is DEBT (see `core.classify`); an unknown category counts as equity, the cautious reading.

Shocks (fractions): equity -10/-20/-30%; the largest held sector -30% on top of nothing else; USD/INR +10% and -10%
(only foreign-currency holdings move); joint India + US stress = equity -20% in local terms with INR 5% weaker, so a
US holding moves by 0.8 x 1.05 - 1; commodity reversal: gold and silver -15%."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from arthashreni.core.classify import is_equity_oriented
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp, m_div, m_mul, m_sum, with_status

COMMODITY = frozenset({"GOLD_ETF", "SILVER_ETF", "SGB", "DIGITAL_GOLD"})
D = Decimal
SECTOR_SHOCK = D("-0.30")
COMMODITY_SHOCK = D("-0.15")
JOINT_EQUITY = D("-0.20")
JOINT_INR_WEAKER = D("0.05")


@dataclass(frozen=True)
class Exposure:
    asset_id: int
    name: str
    asset_class: str
    currency: str
    sector: str | None
    value_inr: Measured[Decimal]
    fund_category: str | None = None


@dataclass(frozen=True)
class ScenarioResult:
    id: str
    label: str
    description: str
    per_holding: tuple[tuple[str, Measured[Decimal]], ...]  # asset name -> INR impact (negative = loss)
    total_inr: Measured[Decimal]
    total_pct: Measured[Decimal]  # of the portfolio's valued part
    note: str = "scenario, not a prediction"


Shock = Callable[[Exposure], Decimal]


def _equity(pct: Decimal) -> Shock:
    return lambda e: pct if is_equity_oriented(e.asset_class, e.fund_category) else D(0)


def _fx_shock(pct: Decimal) -> Shock:
    return lambda e: pct if e.currency != "INR" else D(0)


def _run(
    sid: str, label: str, description: str, exposures: list[Exposure], shock: Shock, day: date
) -> ScenarioResult:
    stamp = day_stamp(day)
    valued = [e for e in exposures if e.value_inr.usable]
    unvalued = [e.name for e in exposures if not e.value_inr.usable]
    rows = []
    for e in valued:
        pct = Measured(shock(e), QualityStatus.OK, stamp, ("scenario",), ())
        rows.append((e.name, m_mul(e.value_inr, pct)))
    total = m_sum((m for _, m in rows), stamp)
    base = m_sum((e.value_inr for e in valued), stamp)
    pct_total = m_div(total, base)
    if unvalued:
        note = f"{len(unvalued)} holding(s) have no usable value and are left out: {', '.join(unvalued)}"
        total, pct_total = (
            with_status(total, QualityStatus.ESTIMATED, note),
            with_status(pct_total, QualityStatus.ESTIMATED, note),
        )
    return ScenarioResult(sid, label, description, tuple(rows), total, pct_total)


def run_scenarios(exposures: list[Exposure], day: date) -> list[ScenarioResult]:
    out = [
        _run(
            f"equity_{p}",
            f"Equity {p}%",
            f"All equity-like holdings fall {abs(p)}%.",
            exposures,
            _equity(D(p) / 100),
            day,
        )
        for p in (-10, -20, -30)
    ]
    by_sector: dict[str, Decimal] = {}
    for e in exposures:
        if e.sector and is_equity_oriented(e.asset_class, e.fund_category) and e.value_inr.value is not None:
            by_sector[e.sector] = by_sector.get(e.sector, D(0)) + e.value_inr.value
    if by_sector:
        top = max(by_sector, key=lambda s: (by_sector[s], s))
        out.append(
            _run(
                "sector_shock",
                f"Sector shock: {top} {SECTOR_SHOCK:.0%}",
                f"The largest held sector ({top}) falls {abs(SECTOR_SHOCK):.0%}.",
                exposures,
                lambda e: (
                    SECTOR_SHOCK
                    if e.sector == top and is_equity_oriented(e.asset_class, e.fund_category)
                    else D(0)
                ),
                day,
            )
        )
    out += [
        _run(
            f"usdinr_{'up' if p > 0 else 'down'}_10",
            f"USD/INR {p:+d}%",
            f"The rupee {'weakens' if p > 0 else 'strengthens'} 10% against the dollar; only foreign holdings move.",
            exposures,
            _fx_shock(D(p) / 100),
            day,
        )
        for p in (10, -10)
    ]
    us = (1 + JOINT_EQUITY) * (1 + JOINT_INR_WEAKER) - 1
    out.append(
        _run(
            "joint_india_us",
            "Joint India + US stress",
            "Indian and US equity both fall 20% (US in dollars) while the rupee weakens 5%.",
            exposures,
            lambda e: (
                (us if e.currency != "INR" else JOINT_EQUITY)
                if is_equity_oriented(e.asset_class, e.fund_category)
                else D(0)
            ),
            day,
        )
    )
    out.append(
        _run(
            "commodity_reversal",
            "Commodity reversal",
            "Gold and silver holdings fall 15%.",
            exposures,
            lambda e: COMMODITY_SHOCK if e.asset_class in COMMODITY else D(0),
            day,
        )
    )
    return out
