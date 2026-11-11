"""Statistics on price series: volatility, drawdown, correlation, benchmark comparison (F1).

A series is dated `Measured` closes. Unusable points (missing or quarantined) are dropped and the
result is `estimated` with a note; otherwise the result's status is the worst of the points used and
its `as_of` is the last observation date. Returns are simple returns between consecutive observations.

Observation interval. Volatility is annualised with sqrt(252), which is only right for one return per trading
day. A series is accepted as daily when its median gap is at most 5 calendar days (weekend plus a holiday);
a sparser series (weekly, monthly, statement-only prices) is refused rather than mis-annualised, and a daily
series with a longer gap somewhere is `estimated`, because a return across the gap spans several sessions and
is counted as one. Correlation and benchmark comparison first reduce both series to the dates they share, so
every pair of returns covers the same interval in both series, whatever each series' own gaps.
"""

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext

from arthashreni.analytics import PRECISION
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp, missing, worst

Series = Sequence[tuple[date, Measured[Decimal]]]
TRADING_DAYS = Decimal(252)
MIN_RETURNS = 20  # fewer returns than this give a number that mostly measures noise
MAX_DAILY_GAP_DAYS = 5  # a long weekend plus a holiday; anything sparser is not a daily series


@dataclass(frozen=True)
class _Clean:
    days: list[date]
    px: list[Decimal]
    status: QualityStatus
    sources: tuple[str, ...]
    notes: tuple[str, ...]


def _clean(series: Series) -> _Clean:
    pts = sorted(
        ((d, m) for d, m in series if m.usable and m.value is not None and m.value > 0), key=lambda p: p[0]
    )
    dropped = len(series) - len(pts)
    status = worst(*(m.status for _, m in pts)) if pts else QualityStatus.OK
    # Only points that are not plain `ok` explain a status; the rest of the row notes are ingestion chatter.
    flagged = [m for _, m in pts if m.status is not QualityStatus.OK]
    notes: tuple[str, ...] = (
        (
            f"{len(flagged)} of {len(pts)} observations are not ok: {flagged[0].status.value} "
            f"(e.g. {flagged[0].notes[0] if flagged[0].notes else 'no note'})",
        )
        if flagged
        else ()
    )
    if dropped:
        status = worst(status, QualityStatus.ESTIMATED)
        notes = (*notes, f"{dropped} unusable observation(s) dropped")
    sources = tuple(dict.fromkeys(s for _, m in pts for s in m.source_ids))
    return _Clean(
        [d for d, _ in pts], [m.value for _, m in pts if m.value is not None], status, sources, notes
    )


def _out(value: Decimal, c: _Clean, note: str) -> Measured[Decimal]:
    return Measured(value, c.status, day_stamp(c.days[-1]), c.sources, (*c.notes, note))


def _returns(px: Sequence[Decimal]) -> list[Decimal]:
    return [b / a - 1 for a, b in itertools.pairwise(px)]


def _mean(xs: Sequence[Decimal]) -> Decimal:
    return sum(xs, Decimal(0)) / len(xs)


def _cov(xs: Sequence[Decimal], ys: Sequence[Decimal]) -> Decimal:
    mx, my = _mean(xs), _mean(ys)
    return sum(((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)), Decimal(0)) / (len(xs) - 1)


def volatility(series: Series, min_returns: int = MIN_RETURNS) -> Measured[Decimal]:
    """Annualised standard deviation of simple returns (sample, x sqrt(252))."""
    c = _clean(series)
    if len(c.px) - 1 < min_returns:
        return missing(
            day_stamp(c.days[-1]) if c.days else None,
            f"volatility needs {min_returns} returns, have {max(len(c.px) - 1, 0)}",
        )
    gaps = sorted((b - a).days for a, b in itertools.pairwise(c.days))
    if gaps[len(gaps) // 2] > MAX_DAILY_GAP_DAYS:
        return Measured(
            None,
            QualityStatus.MISSING,
            day_stamp(c.days[-1]),
            c.sources,
            (
                f"not a daily series (median gap {gaps[len(gaps) // 2]} days): "
                "annualising with 252 periods a year would be wrong",
            ),
        )
    long_gaps = sum(1 for g in gaps if g > MAX_DAILY_GAP_DAYS)
    if long_gaps:
        c = replace(
            c,
            status=worst(c.status, QualityStatus.ESTIMATED),
            notes=(
                *c.notes,
                f"{long_gaps} gap(s) longer than {MAX_DAILY_GAP_DAYS} days counted as one return each",
            ),
        )
    with localcontext() as ctx:
        ctx.prec = PRECISION
        r = _returns(c.px)
        vol = _cov(r, r).sqrt() * TRADING_DAYS.sqrt()
    return _out(vol, c, f"annualised from {len(r)} returns, 252 periods a year")


@dataclass(frozen=True)
class Drawdown:
    depth: Measured[Decimal]  # worst peak-to-trough fall as a negative fraction (-0.25 = -25%)
    peak: date | None
    trough: date | None
    recovered_on: date | None  # first day back at the old peak; None while still under water
    recovery_days: int | None  # trough to recovery
    current_depth: Measured[Decimal]  # distance from the running peak today (0 at a new high)


def max_drawdown(series: Series) -> Drawdown:
    c = _clean(series)
    if len(c.px) < 2:
        gone: Measured[Decimal] = missing(
            day_stamp(c.days[-1]) if c.days else None, "drawdown needs at least two prices"
        )
        return Drawdown(gone, None, None, None, None, gone)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        peak_i, worst_depth, worst_peak, worst_trough = 0, Decimal(0), 0, 0
        for i, p in enumerate(c.px):
            if p > c.px[peak_i]:
                peak_i = i
            depth = p / c.px[peak_i] - 1
            if depth < worst_depth:
                worst_depth, worst_peak, worst_trough = depth, peak_i, i
        current = c.px[-1] / max(c.px) - 1
        recovered = next((i for i in range(worst_trough, len(c.px)) if c.px[i] >= c.px[worst_peak]), None)
    if worst_depth == 0:
        return Drawdown(
            _out(Decimal(0), c, "no fall from a peak in this series"),
            None,
            None,
            None,
            None,
            _out(current, c, "from running peak"),
        )
    return Drawdown(
        _out(worst_depth, c, "worst peak-to-trough fall in the series"),
        c.days[worst_peak],
        c.days[worst_trough],
        None if recovered is None else c.days[recovered],
        None if recovered is None else (c.days[recovered] - c.days[worst_trough]).days,
        _out(current, c, "from running peak"),
    )


def _aligned(a: Series, b: Series) -> tuple[_Clean, _Clean]:
    ca, cb = _clean(a), _clean(b)
    common = sorted(set(ca.days) & set(cb.days))
    pa, pb = dict(zip(ca.days, ca.px, strict=True)), dict(zip(cb.days, cb.px, strict=True))
    return (
        _Clean(common, [pa[d] for d in common], ca.status, ca.sources, ca.notes),
        _Clean(common, [pb[d] for d in common], cb.status, cb.sources, cb.notes),
    )


def _merge(a: _Clean, b: _Clean) -> _Clean:
    return _Clean(
        a.days, a.px, worst(a.status, b.status), tuple(dict.fromkeys((*a.sources, *b.sources))),
        tuple(dict.fromkeys((*a.notes, *b.notes))),
    )  # fmt: skip


def correlation(a: Series, b: Series, min_returns: int = MIN_RETURNS) -> Measured[Decimal]:
    """Pearson correlation of simple returns on the dates both series share."""
    ca, cb = _aligned(a, b)
    meta = _merge(ca, cb)
    n = len(ca.px) - 1
    if n < min_returns:
        return missing(
            day_stamp(ca.days[-1]) if ca.days else None,
            f"correlation needs {min_returns} common returns, have {max(n, 0)}",
        )
    with localcontext() as ctx:
        ctx.prec = PRECISION
        ra, rb = _returns(ca.px), _returns(cb.px)
        va, vb = _cov(ra, ra), _cov(rb, rb)
        if va == 0 or vb == 0:
            return Measured(
                None,
                QualityStatus.MISSING,
                day_stamp(ca.days[-1]),
                meta.sources,
                (*meta.notes, "a series has no variation"),
            )
        rho = _cov(ra, rb) / (va * vb).sqrt()
    return _out(rho, meta, f"{n} common returns")


@dataclass(frozen=True)
class BenchmarkComparison:
    benchmark_id: str
    asset_return: Measured[Decimal]
    benchmark_return: Measured[Decimal]
    excess_return: Measured[Decimal]  # asset minus benchmark over the same dates (not annualised)
    beta: Measured[Decimal]  # slope of asset returns on benchmark returns
    correlation: Measured[Decimal]


def compare_to_benchmark(
    asset: Series, benchmark: Series, benchmark_id: str, min_returns: int = MIN_RETURNS
) -> BenchmarkComparison:
    """Asset vs a documented benchmark (`benchmarks.py`) over the dates both have prices."""
    ca, cb = _aligned(asset, benchmark)
    meta = _merge(ca, cb)
    n = len(ca.px) - 1
    if n < min_returns:
        gone: Measured[Decimal] = missing(
            day_stamp(ca.days[-1]) if ca.days else None,
            f"benchmark comparison needs {min_returns} common returns, have {max(n, 0)}",
        )
        return BenchmarkComparison(benchmark_id, gone, gone, gone, gone, gone)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        r_asset, r_bench = ca.px[-1] / ca.px[0] - 1, cb.px[-1] / cb.px[0] - 1
        ra, rb = _returns(ca.px), _returns(cb.px)
        var_b = _cov(rb, rb)
        beta = None if var_b == 0 else _cov(ra, rb) / var_b
    note = f"{ca.days[0]} to {ca.days[-1]}, {n} common returns, benchmark {benchmark_id}"
    gone = Measured[Decimal](
        None,
        QualityStatus.MISSING,
        day_stamp(ca.days[-1]),
        meta.sources,
        (*meta.notes, "benchmark has no variation"),
    )
    return BenchmarkComparison(
        benchmark_id,
        _out(r_asset, meta, note),
        _out(r_bench, meta, note),
        _out(r_asset - r_bench, meta, note),
        gone if beta is None else _out(beta, meta, note),
        correlation(asset, benchmark, min_returns),
    )
