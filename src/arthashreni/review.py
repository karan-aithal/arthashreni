"""Service layer for the risk run and the monthly review: loads everything for one dataset and calls the pure
`risk`, `policy` and `analytics` code. Like `portfolio.py`, this is the only place that joins them to the database,
so the engines stay free of I/O and the numbers in a recommendation all come from tested Python."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.analytics.benchmarks import BENCHMARKS, benchmark_for
from arthashreni.analytics.series import Series, compare_to_benchmark, correlation
from arthashreni.core.config import PROJECT_ROOT, Dataset
from arthashreni.db.as_of import fx_as_of, price_series
from arthashreni.dq.confidence import Confidence, ConfidenceInput, confidence
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp, missing, worst
from arthashreni.ledger.reconciliation import open_differences
from arthashreni.policy import store as pstore
from arthashreni.policy.context import Certainty
from arthashreni.policy.cost import load_costs
from arthashreni.policy.decision import DecisionInput, Holding, PastMove, Reserve, decide
from arthashreni.policy.envelope import sip_lines
from arthashreni.policy.recommendation import Recommendation
from arthashreni.portfolio import STATS_DAYS, PortfolioReport, _benchmark_asset, build_report
from arthashreni.risk import store as rstore
from arthashreni.risk.alerts import Alert, AlertDiff, build_alerts, diff_alerts
from arthashreni.risk.levels import LevelResult, overall_level, vetoes
from arthashreni.risk.scenarios import Exposure, ScenarioResult, run_scenarios
from arthashreni.risk.signals import (
    Decline,
    Scope,
    Signal,
    band_breach_signals,
    classify_decline,
    correlation_rise_signal,
    emergency_months_signal,
    foreign_share_signal,
    holding_signal,
    index_signals,
    macro_missing,
    make,
    top3_signal,
    usdinr_signal,
)
from arthashreni.risk.thresholds import Level, load

SHORT_DAYS = 60
FX_DAYS = 30
ZERO = Decimal(0)


@dataclass(frozen=True)
class RiskRun:
    day: date
    signals: tuple[Signal, ...]
    level: LevelResult
    declines: dict[int, Decline]
    vetoes: dict[int, tuple[str, ...]]
    scenarios: tuple[ScenarioResult, ...]
    alerts: tuple[Alert, ...]
    diff: AlertDiff
    confidence: Confidence


@dataclass(frozen=True)
class MonthlyReview:
    recommendation: Recommendation
    risk: RiskRun
    report: PortfolioReport
    ips_row_id: int | None


def _cross_checked(db: Session, asset_id: int, day: date) -> bool:
    """A second source reported a price for this asset on its latest price date."""
    row = db.execute(
        text(
            """SELECT count(DISTINCT source_id) FROM prices WHERE asset_id = :a AND quality_status <> 'quarantined'
               AND valid_at = (SELECT max(valid_at) FROM prices WHERE asset_id = :a AND valid_at <= :d)"""
        ),
        {"a": asset_id, "d": day},
    ).scalar_one()
    return int(row) >= 2


def _confidence(db: Session, report: PortfolioReport, day: date) -> Confidence:
    inputs = []
    for h in report.holdings:
        status = worst(h.price.status, *((h.fx.status,) if h.fx else ()))
        weight = (
            h.value_inr.value if h.value_inr.usable and h.value_inr.value is not None else h.cost_inr.value
        )
        inputs.append(ConfidenceInput(weight or ZERO, status, _cross_checked(db, h.position.asset.id, day)))
    return confidence(inputs)


def _window(series: Series, since: date) -> Series:
    return [(d, m) for d, m in series if d >= since]


def _index_signals(db: Session, dataset: Dataset, day: date) -> tuple[list[Signal], dict[str, Series]]:
    cfg = load()
    start = day - timedelta(days=STATS_DAYS)
    signals: list[Signal] = []
    bench: dict[str, Series] = {}
    for b in BENCHMARKS:
        bid = _benchmark_asset(db, dataset, b.ticker)
        bench[b.id] = price_series(db, bid, start, day) if bid is not None else []
        if bench[b.id]:
            signals += index_signals(cfg, b.id, bench[b.id])
        else:
            note: Measured[Decimal] = missing(note=f"no {b.ticker} index series imported")
            signals += [make(cfg, k, Scope.MARKET, b.id, note) for k in ("idx_drawdown", "idx_vol")]
    now = fx_as_of(db, "FBIL_REF", day)
    signals.append(usdinr_signal(cfg, now, fx_as_of(db, "FBIL_REF", day - timedelta(days=FX_DAYS))))
    return signals + macro_missing(), bench


def _context_signals(db: Session, report: PortfolioReport, day: date) -> list[Signal]:
    cfg = load()
    signals: list[Signal] = []
    ips = pstore.approved_ips(db)
    if ips is not None:
        weights = {
            (dim, s.key): s.weight
            for dim in ("asset_class", "country", "currency")
            for s in report.allocation[dim]
        }
        bands = {
            (dim, k): (b.low, b.high)
            for dim, mapping in (
                ("asset_class", ips[2].asset_class_bands),
                ("country", ips[2].country_bands),
                ("currency", ips[2].currency_bands),
            )
            for k, b in mapping.items()
        }
        signals += band_breach_signals(cfg, weights, bands)
    ctx = pstore.load_context(db)
    fund, spend = ctx.get("emergency_fund_inr"), ctx.get("essential_expenses_inr")
    known = [f for f in (fund, spend) if f is not None and f.certainty is not Certainty.UNKNOWN]
    if len(known) == 2 and fund is not None and spend is not None and Decimal(str(spend.value)) > 0:
        assumed = Certainty.ASSUMED in (fund.certainty, spend.certainty)
        months = Measured(
            Decimal(str(fund.value)) / Decimal(str(spend.value)),
            QualityStatus.ESTIMATED if assumed else QualityStatus.OK,
            day_stamp(day),
            ("personal_context",),
        )
        signals.append(emergency_months_signal(cfg, months))
    else:
        signals.append(
            emergency_months_signal(cfg, missing(note="emergency fund or essential expenses unknown"))
        )
    return signals


def run_risk(
    db: Session, dataset: Dataset, day: date, *, persist: bool = False, report: PortfolioReport | None = None
) -> RiskRun:
    cfg = load()
    report = report or build_report(db, dataset, day)
    start = day - timedelta(days=STATS_DAYS)
    signals, bench = _index_signals(db, dataset, day)

    assets = {h.position.asset.id: h.position.asset for h in report.holdings}
    values: dict[int, Measured[Decimal]] = {}
    for h in report.holdings:
        values.setdefault(h.position.asset.id, h.value_inr)
    by_asset = {a_id: price_series(db, a_id, start, day) for a_id in assets}
    declines: dict[int, Decline] = {}
    for a_id, series in by_asset.items():
        sig = holding_signal(cfg, a_id, assets[a_id].name, series)
        signals.append(sig)
        if sig.level is not None and sig.level >= Level.CAUTION:
            b = benchmark_for(assets[a_id].asset_class)
            cmp = None if b is None else compare_to_benchmark(series, bench[b.id], b.id)
            declines[a_id] = classify_decline(cfg, cmp)

    signals.append(top3_signal(cfg, report.concentration.top_n_weight))
    signals.append(foreign_share_signal(cfg, {s.key: s.weight for s in report.allocation["currency"]}))
    ids = sorted(by_asset)
    cut = day - timedelta(days=SHORT_DAYS)
    pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]]
    short = [correlation(_window(by_asset[a], cut), _window(by_asset[b], cut)) for a, b in pairs]
    long = [correlation(by_asset[a], by_asset[b]) for a, b in pairs]
    signals.append(correlation_rise_signal(cfg, short, long))
    signals += _context_signals(db, report, day)

    exposures = [
        Exposure(a.id, a.name, a.asset_class, a.currency, a.sector, values[a.id], a.fund_category)
        for a in assets.values()
    ]
    scenarios = run_scenarios(exposures, day)
    level = overall_level(signals, cfg.rules)
    found = vetoes(signals, declines)
    alerts = build_alerts(
        day, signals, declines, values, tuple(s.id for s in scenarios), cfg.rules.alert_min_level
    )
    diff = diff_alerts(alerts, rstore.open_alert_keys(db))
    if persist:
        rstore.save_signals(db, day, signals)
        rstore.save_level(db, day, level)
        rstore.apply_alert_diff(db, diff)
    return RiskRun(
        day, tuple(signals), level, declines, found, tuple(scenarios), tuple(alerts), diff,
        _confidence(db, report, day),
    )  # fmt: skip


def _costs_path(dataset: Dataset) -> Path:
    return PROJECT_ROOT / ("demo" if dataset == "demo" else "config") / "costs.toml"


def _history(db: Session, before: date) -> tuple[PastMove, ...]:
    rows = db.execute(
        text(
            """SELECT DISTINCT r.period, a.asset_class, l.action FROM decisions d
               JOIN recommendation_lines l ON l.id = d.line_id JOIN recommendations r ON r.id = l.recommendation_id
               JOIN ref.assets a ON a.id = l.asset_id
               WHERE d.decision = 'APPROVE' AND l.action IN ('accumulate', 'reduce') AND r.period < :p"""
        ),
        {"p": before},
    ).all()
    return tuple(PastMove(r[0], r[1], "up" if r[2] == "accumulate" else "down") for r in rows)


def _holdings(db: Session, report: PortfolioReport, risk: RiskRun, failed: set[int]) -> list[Holding]:
    platforms = {int(r[0]): str(r[1]) for r in db.execute(text("SELECT id, platform FROM accounts")).all()}
    seen: dict[int, Holding] = {}
    for hv in sorted(report.holdings, key=lambda x: -(x.value_inr.value or ZERO)):
        a = hv.position.asset
        value = hv.value_inr.value or ZERO
        if a.id in seen:  # same asset in another account: one holding, platform of the larger position
            seen[a.id] = replace(seen[a.id], value_inr=seen[a.id].value_inr + value)
            continue
        seen[a.id] = Holding(
            a.id, a.name, a.asset_class, a.country, a.currency, platforms.get(hv.position.account_id, "OTHER"),
            value, hv.price.status, hv.price.as_of.date(), a.sector, a.fund_house, a.id in failed,
            risk.vetoes.get(a.id, ()), theme=a.theme, fund_category=a.fund_category,
        )  # fmt: skip
    return list(seen.values())


def monthly_review(
    db: Session,
    dataset: Dataset,
    period: date,
    today: date,
    *,
    persist_risk: bool = False,
    reserve_use: tuple[tuple[int, Decimal], ...] = (),
) -> MonthlyReview:
    """Reconcile, run risk and policy, and produce the recommendation. Nothing is stored except (optionally) the
    risk run; storing the month's allocation is a separate, explicit step (`pstore.allocate_period`)."""
    budget = pstore.budget_for(db, period)
    if budget is None:
        raise pstore.PolicyError("no monthly budget is configured: run `budget set` first")
    report = build_report(db, dataset, today)
    risk = run_risk(db, dataset, today, persist=persist_risk, report=report)
    ips = pstore.approved_ips(db)
    ctx = pstore.load_context(db)
    flagged = ctx.get("thesis_failed_asset_ids")
    failed = {int(x) for x in (flagged.value or [])} if flagged else set()
    balances = pstore.reserve_balances(db)
    cash = next((b for b in balances if b.kind == "CASH_RESERVE"), None)
    ipo = next((b for b in balances if b.kind == "IPO_SINKING_FUND"), None)
    scenarios: tuple[dict[str, Any], ...] = tuple(
        {
            "id": s.id, "label": s.label, "note": s.note, "status": s.total_inr.status.value,
            "total_inr": None if s.total_inr.value is None else str(s.total_inr.value),
            "total_pct": None if s.total_pct.value is None else str(s.total_pct.value),
        }
        for s in risk.scenarios
    )  # fmt: skip
    notes = (
        (
            f"risk level rests on {risk.level.available} of {risk.level.total} signals; no data for: "
            + ", ".join(risk.level.missing),
        )
        if risk.level.missing
        else ()
    )
    rec = decide(
        DecisionInput(
            period=period, today=today, dataset=dataset.upper(), opening_budget_inr=budget[1],
            sip_lines=tuple(sip_lines(pstore.load_mandates(db), period)),
            holdings=tuple(_holdings(db, report, risk, failed)),
            reconciliation_clean=not open_differences(db), confidence=risk.confidence.level,
            confidence_reasons=risk.confidence.reasons, risk_level=risk.level.level,
            ips=None if ips is None else ips[2], ips_version=None if ips is None else ips[1], context=ctx,
            costs=load_costs(_costs_path(dataset)),
            cash_reserve=None if cash is None else Reserve(cash.id, cash.name, cash.available_inr),
            ipo_reserve=None if ipo is None else Reserve(ipo.id, ipo.name, ipo.available_inr),
            history=_history(db, period), reserve_use=reserve_use, scenarios=scenarios,
            sources=tuple(sorted({s for hv in report.holdings for s in hv.price.source_ids})),
            risk_notes=notes,
        )
    )  # fmt: skip
    return MonthlyReview(rec, risk, report, None if ips is None else ips[0])
