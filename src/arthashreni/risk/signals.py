"""Risk signals (F2): market-wide and portfolio-level, each a `Measured` value with a level taken from the
threshold table. A signal whose input is missing has no level and says so; it never counts as "normal"."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from arthashreni.analytics.series import BenchmarkComparison, Series, max_drawdown, volatility
from arthashreni.dq.measured import Measured, QualityStatus, m_div, m_sub, missing
from arthashreni.risk.thresholds import Config, Level, Threshold

ZERO = Decimal(0)


class Scope(StrEnum):
    MARKET = "MARKET"
    PORTFOLIO = "PORTFOLIO"
    ASSET = "ASSET"


@dataclass(frozen=True)
class Signal:
    id: str
    kind: str  # key into the threshold table ("" for a signal with no feed)
    scope: Scope
    subject: str  # benchmark id, asset name or "portfolio"
    value: Measured[Decimal]
    level: Level | None
    asset_id: int | None = None


def level_of(value: Measured[Decimal], th: Threshold) -> Level | None:
    if not value.usable or value.value is None:
        return None
    v = value.value
    sign = 1 if th.direction == "above" else -1
    v = v * sign
    if v >= th.severe * sign:
        return Level.SEVERE_STRESS
    if v >= th.high * sign:
        return Level.HIGH_RISK
    return Level.CAUTION if v >= th.caution * sign else Level.NORMAL


def make(
    cfg: Config, kind: str, scope: Scope, subject: str, value: Measured[Decimal], asset_id: int | None = None
) -> Signal:
    return Signal(
        f"{kind}:{subject}", kind, scope, subject, value, level_of(value, cfg.thresholds[kind]), asset_id
    )


def _negate(m: Measured[Decimal]) -> Measured[Decimal]:
    return m if m.value is None else replace(m, value=-m.value)


def index_signals(cfg: Config, bench_id: str, series: Series) -> list[Signal]:
    depth = max_drawdown(series).current_depth
    return [
        make(cfg, "idx_drawdown", Scope.MARKET, bench_id, _negate(depth)),
        make(cfg, "idx_vol", Scope.MARKET, bench_id, volatility(series)),
    ]


def usdinr_signal(cfg: Config, now: Measured[Decimal], then: Measured[Decimal]) -> Signal:
    change = m_div(m_sub(now, then), then)
    if change.value is not None:
        change = replace(change, value=abs(change.value))
    return make(cfg, "usdinr_move", Scope.MARKET, "USDINR", change)


def macro_missing(
    as_of_note: str = "no macro feed (FRED, RBI DBIE and MOSPI adapters are not built)",
) -> list[Signal]:
    """Rates, inflation and oil have no feed yet: shown as missing, never guessed (D1, invariant 6)."""
    return [
        Signal(f"macro:{name}", "", Scope.MARKET, name, missing(note=as_of_note), None)
        for name in ("rates", "inflation", "oil")
    ]


def holding_signal(cfg: Config, asset_id: int, name: str, series: Series) -> Signal:
    return make(
        cfg, "holding_drawdown", Scope.ASSET, name, _negate(max_drawdown(series).current_depth), asset_id
    )


def top3_signal(cfg: Config, top3: Measured[Decimal]) -> Signal:
    return make(cfg, "top3_weight", Scope.PORTFOLIO, "portfolio", top3)


def foreign_share_signal(cfg: Config, currency_weights: Mapping[str, Measured[Decimal]]) -> Signal:
    if not currency_weights:
        return make(cfg, "foreign_share", Scope.PORTFOLIO, "portfolio", missing(note="no valued holdings"))
    foreign = [w for k, w in currency_weights.items() if k != "INR"]
    status = max((w.status for w in currency_weights.values()), key=lambda s: s.severity)
    any_w = next(iter(currency_weights.values()))
    if any(w.value is None for w in currency_weights.values()):
        return make(
            cfg, "foreign_share", Scope.PORTFOLIO, "portfolio", missing(any_w.as_of, "a weight is missing")
        )
    total = sum((w.value or ZERO for w in foreign), ZERO)
    return make(
        cfg,
        "foreign_share",
        Scope.PORTFOLIO,
        "portfolio",
        Measured(total, status, any_w.as_of, any_w.source_ids, any_w.notes),
    )


def band_breach_signals(
    cfg: Config,
    weights: Mapping[tuple[str, str], Measured[Decimal]],
    bands: Mapping[tuple[str, str], tuple[Decimal, Decimal]],
) -> list[Signal]:
    """One signal per banded (dimension, key): the distance outside its IPS band, 0 while inside."""
    out: list[Signal] = []
    for (dim, key), (low, high) in sorted(bands.items()):
        w = weights.get((dim, key)) or Measured(
            ZERO, QualityStatus.OK, missing().as_of, ("ledger",), ("not held",)
        )
        if w.value is not None:
            w = replace(w, value=max(ZERO, w.value - high, low - w.value))
        out.append(make(cfg, "band_breach", Scope.PORTFOLIO, f"{dim}:{key}", w))
    return out


def correlation_rise_signal(
    cfg: Config, short: Sequence[Measured[Decimal]], long: Sequence[Measured[Decimal]]
) -> Signal:
    """Average pairwise correlation over a short window minus over a long one. Needs every pair on both sides."""
    if not short or not long or any(not m.usable for m in (*short, *long)):
        return make(
            cfg, "correlation_rise", Scope.PORTFOLIO, "portfolio", missing(note="not enough price history")
        )

    def avg(ms: Sequence[Measured[Decimal]]) -> Decimal:
        return sum((m.value or ZERO for m in ms), ZERO) / len(ms)

    from arthashreni.dq.measured import worst

    status = worst(*(m.status for m in (*short, *long)))
    first = short[0]
    return make(
        cfg,
        "correlation_rise",
        Scope.PORTFOLIO,
        "portfolio",
        Measured(
            avg(short) - avg(long),
            status,
            min(m.as_of for m in (*short, *long)),
            first.source_ids,
            first.notes,
        ),
    )


def emergency_months_signal(cfg: Config, months: Measured[Decimal]) -> Signal:
    return make(cfg, "emergency_months", Scope.PORTFOLIO, "portfolio", months)


# ---- market-wide vs asset-specific --------------------------------------------------------------------


@dataclass(frozen=True)
class Decline:
    kind: str  # market-wide | asset-specific | uncertain
    reason: str


def classify_decline(cfg: Config, cmp: BenchmarkComparison | None) -> Decline:
    """Compare the asset's fall with its benchmark's over the same dates. Uncertain is an allowed answer;
    fundamentals and filings are not available before Phase 7, so they cannot settle a doubtful case."""
    if cmp is None or not cmp.asset_return.usable or not cmp.benchmark_return.usable:
        return Decline("uncertain", "no usable benchmark comparison")
    a, b = cmp.asset_return.value, cmp.benchmark_return.value
    if a is None or b is None or a >= 0:
        return Decline("uncertain", "the asset has not fallen over the compared dates")
    corr = cmp.correlation.value
    if corr is None or corr < cfg.rules.min_correlation:
        return Decline("uncertain", "the benchmark is not correlated enough to be a fair yardstick")
    ratio = (-b) / (-a)  # >0 when the benchmark fell too
    if ratio >= cfg.rules.market_wide_ratio:
        return Decline("market-wide", f"the benchmark fell {ratio:.0%} as much as the asset")
    if ratio <= cfg.rules.asset_specific_ratio:
        return Decline(
            "asset-specific", f"the benchmark fell only {max(ratio, ZERO):.0%} as much as the asset"
        )
    return Decline("uncertain", f"the benchmark fell {ratio:.0%} as much as the asset: in between")
