"""Portfolio report: loads the ledger, prices and FX for one dataset and runs the calculation engine.

`analytics` stays pure; this module is the only place that joins it to the database. Every figure in
the report is a `Measured`, and a missing price or rate shows as missing rather than as zero.
"""

import itertools
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.analytics.allocation import (
    Concentration,
    Dimension,
    LookThrough,
    Slice,
    allocate,
    concentration,
    look_through,
)
from arthashreni.analytics.benchmarks import benchmark_for
from arthashreni.analytics.flows import cash_flows, has_internal_moves, paid_in, twr_flow
from arthashreni.analytics.fx import FxAttribution, attribute_fx
from arthashreni.analytics.returns import ReturnMeasure, ValuePoint, absolute_return, twr
from arthashreni.analytics.series import (
    BenchmarkComparison,
    Drawdown,
    Series,
    compare_to_benchmark,
    correlation,
    max_drawdown,
    volatility,
)
from arthashreni.analytics.valuation import (
    AssetInfo,
    HoldingValue,
    IncomeAndCosts,
    Position,
    Realised,
    Totals,
    from_ledger,
    income_and_costs,
    positions_from_book,
    realised_pnl,
    totals,
    value_position,
)
from arthashreni.analytics.xirr import xirr
from arthashreni.core.config import Dataset
from arthashreni.db.as_of import FxRateType, price_as_of, price_series, valuation_fx_typed
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp, m_add, m_sum, with_status
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.repository import load_actions, load_effective
from arthashreni.ledger.types import ActionType, CorporateAction, TxnRow

ALLOCATION_DIMENSIONS: tuple[Dimension, ...] = ("asset_class", "country", "currency", "sector", "market_cap")
STATS_DAYS = 365  # window for volatility, drawdown, correlation and benchmark comparison


@dataclass(frozen=True)
class AssetStats:
    asset: AssetInfo
    volatility: Measured[Decimal]
    drawdown: Drawdown


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    correlation: Measured[Decimal]


@dataclass(frozen=True)
class BenchmarkRow:
    asset: str
    comparison: BenchmarkComparison


@dataclass(frozen=True)
class FxRow:
    asset: str
    attribution: FxAttribution


@dataclass(frozen=True)
class AssetReturn:
    asset: str
    xirr: ReturnMeasure


@dataclass(frozen=True)
class PortfolioReport:
    dataset: str
    as_of: date
    fx_rate_type: str | None
    holdings: tuple[HoldingValue, ...]
    totals: Totals
    invested_inr: Measured[Decimal]  # money paid in over time, including money since returned
    realised: Realised
    income: IncomeAndCosts
    returns: tuple[ReturnMeasure, ...]  # portfolio XIRR, TWR, absolute return
    asset_returns: tuple[AssetReturn, ...]
    fx_attribution: tuple[FxRow, ...]
    allocation: dict[str, list[Slice]]
    concentration: Concentration
    look_through: LookThrough
    stats: tuple[AssetStats, ...]
    correlations: tuple[Pair, ...]
    benchmarks: tuple[BenchmarkRow, ...]


def load_assets(db: Session, ids: set[int]) -> dict[int, AssetInfo]:
    if not ids:
        return {}
    rows = db.execute(
        text(
            """SELECT id, name, asset_class, currency, country, sector, market_cap_bucket, fund_house
               FROM ref.assets WHERE id = ANY(:ids)"""
        ),
        {"ids": sorted(ids)},
    ).all()
    return {r[0]: AssetInfo(*r) for r in rows}


def _value_all(
    db: Session,
    positions: list[Position],
    day: date,
    known_at: datetime | None,
) -> tuple[list[HoldingValue], str | None]:
    fx: Measured[Decimal] | None = None
    fx_type: FxRateType | None = None
    out: list[HoldingValue] = []
    for p in positions:
        price = price_as_of(db, p.asset.id, day, known_at)
        if p.asset.currency != "INR" and fx is None:
            fx, fx_type = valuation_fx_typed(db, day, known_at)  # one lookup per day, not per holding
        out.append(value_position(p, price, fx if p.asset.currency != "INR" else None, fx_type, day))
    return out, fx_type


def _portfolio_value(
    db: Session,
    txns: list[TxnRow],
    actions: list[CorporateAction],
    assets: dict[int, AssetInfo],
    day: date,
    known_at: datetime | None,
) -> Totals:
    book = build_lots(txns, actions, day)
    held, _ = _value_all(db, positions_from_book(book, assets), day, known_at)
    return totals(held, day)


def _asset_value(holdings: list[HoldingValue], asset_id: int, day: date) -> Measured[Decimal]:
    mine = [h.value_inr for h in holdings if h.position.asset.id == asset_id]
    return m_sum(mine, day_stamp(day)) if mine else from_ledger(Decimal(0), day_stamp(day), "no units held")


def _returns(
    db: Session,
    txns: list[TxnRow],
    actions: list[CorporateAction],
    assets: dict[int, AssetInfo],
    t: Totals,
    gain: Measured[Decimal],
    invested: Measured[Decimal],
    day: date,
    known_at: datetime | None,
) -> tuple[ReturnMeasure, ...]:
    flows = cash_flows(txns, until=day)
    x = xirr([*flows, *([(day, t.value_inr.value)] if t.value_inr.value else [])])
    if t.value_inr.status is not QualityStatus.OK or t.unvalued:
        x = with_status(x, t.value_inr.status, "ending value: " + "; ".join(t.value_inr.notes or ("not ok",)))
    xirr_m = ReturnMeasure(
        "XIRR", "money-weighted: the investor chose when to add money, so timing counts", x
    )
    points: list[ValuePoint] = []
    for d in sorted({f[0] for f in flows} | {day}):
        v = t.value_inr if d == day else _portfolio_value(db, txns, actions, assets, d, known_at).value_inr
        points.append(ValuePoint(d, v, twr_flow(flows, d)))
    return (xirr_m, twr(points), absolute_return(gain, invested))


def _lineage(actions: list[CorporateAction], ids: set[int], day: date) -> dict[int, set[int]]:
    """Current asset id -> itself plus every asset merged into it, so one holding has one return."""
    successor = {
        a.asset_id: a.successor_asset_id
        for a in actions
        if a.action_type is ActionType.MERGER and a.successor_asset_id is not None and a.ex_date <= day
    }
    groups: dict[int, set[int]] = {}
    for i in ids:
        root = i
        while root in successor:
            root = successor[root]
        groups.setdefault(root, set()).add(i)
    return groups


def _asset_returns(
    txns: list[TxnRow],
    actions: list[CorporateAction],
    holdings: list[HoldingValue],
    assets: dict[int, AssetInfo],
    day: date,
) -> tuple[AssetReturn, ...]:
    out: list[AssetReturn] = []
    for root, members in sorted(_lineage(actions, set(assets), day).items(), key=lambda g: assets[g[0]].name):
        flows = cash_flows(txns, asset_ids=members, until=day)
        if not flows:
            continue
        end = _asset_value(holdings, root, day)
        if end.value is None:
            x = Measured[Decimal](
                None, end.status, end.as_of, end.source_ids, (*end.notes, "ending value missing")
            )
        else:
            x = xirr([*flows, *([(day, end.value)] if end.value else [])])
            if end.status is not QualityStatus.OK:
                x = with_status(x, end.status, "ending value: " + "; ".join(end.notes or ("not ok",)))
        if has_internal_moves(txns, members):
            x = with_status(
                x,
                QualityStatus.ESTIMATED,
                "asset has unit moves without cash (switch/transfer/reinvest): approximate",
            )
        out.append(
            AssetReturn(
                assets[root].name, ReturnMeasure("XIRR", "money-weighted return of this asset alone", x)
            )
        )
    return tuple(out)


def _fx_rows(holdings: list[HoldingValue]) -> tuple[FxRow, ...]:
    rows: list[FxRow] = []
    for h in holdings:
        if h.fx is None or h.fx_rate_type is None:
            continue
        stamp = h.units.as_of
        rows.append(
            FxRow(
                h.position.asset.name,
                attribute_fx(
                    from_ledger(h.position.cost_orig, stamp),
                    h.cost_inr,
                    h.value_local,
                    h.fx,
                    h.fx_rate_type,
                ),
            )
        )
    return tuple(rows)


def _benchmark_asset(db: Session, dataset: Dataset, ticker: str) -> int | None:
    row = db.execute(
        text("SELECT id FROM ref.assets WHERE ticker = :t AND is_demo = :d ORDER BY id LIMIT 1"),
        {"t": ticker, "d": dataset == "demo"},
    ).first()
    return None if row is None else int(row[0])


def _stats(
    db: Session, dataset: Dataset, held: list[AssetInfo], day: date, known_at: datetime | None
) -> tuple[tuple[AssetStats, ...], tuple[Pair, ...], tuple[BenchmarkRow, ...]]:
    start = day - timedelta(days=STATS_DAYS)
    series: dict[int, Series] = {a.id: price_series(db, a.id, start, day, known_at) for a in held}
    stats = tuple(AssetStats(a, volatility(series[a.id]), max_drawdown(series[a.id])) for a in held)
    pairs = tuple(
        Pair(a.name, b.name, correlation(series[a.id], series[b.id]))
        for a, b in itertools.combinations(held, 2)
    )
    bench: list[BenchmarkRow] = []
    cache: dict[str, Series] = {}
    for a in held:
        b = benchmark_for(a.asset_class)
        if b is None:
            continue
        if b.id not in cache:
            bid = _benchmark_asset(db, dataset, b.ticker)
            cache[b.id] = [] if bid is None else price_series(db, bid, start, day, known_at)
        bench.append(BenchmarkRow(a.name, compare_to_benchmark(series[a.id], cache[b.id], b.id)))
    return stats, pairs, tuple(bench)


def build_report(
    db: Session,
    dataset: Dataset,
    day: date,
    known_at: datetime | None = None,
    fund_holdings: dict[int, dict[str, Decimal]] | None = None,
) -> PortfolioReport:
    """Everything the investor sees about the portfolio on `day`, using only data known by `known_at`.

    `fund_holdings` (fund asset id to underlying weights) is empty until fund-house files are imported
    (Phase 7); look-through then lists every fund as having no data.
    """
    txns, actions = load_effective(db), load_actions(db)
    book = build_lots(txns, actions, day)
    asset_ids = {lot.asset_id for lot in book.lots} | {t.asset_id for t in txns if t.asset_id is not None}
    assets = load_assets(db, asset_ids)
    holdings, fx_type = _value_all(db, positions_from_book(book, assets), day, known_at)
    t = totals(holdings, day)
    _, realised = realised_pnl(book, day)
    flows = cash_flows(txns, until=day)
    invested = from_ledger(paid_in(flows), day_stamp(day), "purchases, fees and taxes paid, over time")
    gain = m_add(t.value_inr, from_ledger(sum((cf for _, cf in flows), Decimal(0)), day_stamp(day)))
    held_assets = sorted(
        {h.position.asset.id: h.position.asset for h in holdings}.values(), key=lambda a: a.name
    )
    stats, pairs, bench = _stats(db, dataset, held_assets, day, known_at)
    return PortfolioReport(
        dataset=dataset,
        as_of=day,
        fx_rate_type=fx_type,
        holdings=tuple(holdings),
        totals=t,
        invested_inr=invested,
        realised=realised,
        income=income_and_costs(txns, day),
        returns=_returns(db, txns, actions, assets, t, gain, invested, day, known_at),
        asset_returns=_asset_returns(txns, actions, holdings, assets, day),
        fx_attribution=_fx_rows(holdings),
        allocation={d: allocate(holdings, d) for d in ALLOCATION_DIMENSIONS},
        concentration=concentration(holdings),
        look_through=look_through(holdings, fund_holdings or {}),
        stats=stats,
        correlations=pairs,
        benchmarks=bench,
    )
