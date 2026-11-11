"""Pure E3 checks. No database, no network. Money and rates are `Decimal`."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise

from arthashreni.dq.calendars import Market, calendar_is_assumed, expected_latest
from arthashreni.dq.measured import QualityStatus
from arthashreni.dq.params import (
    JUMP_FLOOR,
    JUMP_K,
    JUMP_MIN_RETURNS,
    JUMP_WINDOW,
    SPLIT_WINDOW_DAYS,
)


@dataclass(frozen=True)
class CheckResult:
    status: QualityStatus
    note: str | None = None


OK = CheckResult(QualityStatus.OK)


def staleness(
    latest_valid_at: date | None,
    today: date,
    market: Market,
    delay_days: int,
    observed: Collection[date] = (),
) -> CheckResult:
    """Missing on a holiday is ok; missing on a trading day is stale (E3)."""
    if latest_valid_at is None:
        return CheckResult(QualityStatus.MISSING, "no observation")
    expected = expected_latest(market, today, delay_days, observed)
    if latest_valid_at >= expected:
        return OK
    behind = (expected - latest_valid_at).days
    note = f"latest {latest_valid_at}, expected {expected} ({behind} days behind)"
    if calendar_is_assumed(market, observed):
        note += f"; {market} calendar not yet known, weekdays assumed"
    return CheckResult(QualityStatus.STALE, note)


def impossible_value(kind: str, value: Decimal) -> CheckResult:
    """FX <= 0, NAV <= 0, price <= 0 (E3)."""
    if value <= 0:
        return CheckResult(QualityStatus.QUARANTINED, f"impossible {kind}: {value} is not positive")
    return OK


def _stdev(xs: Sequence[Decimal]) -> Decimal:
    n = len(xs)
    mean = sum(xs, Decimal(0)) / n
    var = sum(((x - mean) ** 2 for x in xs), Decimal(0)) / (n - 1)
    return var.sqrt()


def daily_returns(history: Sequence[Decimal]) -> list[Decimal]:
    return [b / a - 1 for a, b in pairwise(history) if a > 0]


def jump(
    history: Sequence[Decimal],
    new: Decimal,
    kind: str,
    *,
    action_dates: Collection[date] = (),
    on: date | None = None,
) -> CheckResult:
    """A move beyond k x rolling volatility with no matching corporate action is quarantined (E3).

    `history` is the earlier closes, oldest first. Too little history means the check cannot judge:
    the result is ok with a note, never a silent pass.
    """
    rets = daily_returns(history[-(JUMP_WINDOW + 1) :])
    if not history or history[-1] <= 0:
        return CheckResult(QualityStatus.OK, "jump check skipped: no usable previous value")
    if len(rets) < JUMP_MIN_RETURNS:
        return CheckResult(QualityStatus.OK, f"jump check skipped: only {len(rets)} returns of history")
    move = new / history[-1] - 1
    threshold = max(JUMP_K * _stdev(rets), JUMP_FLOOR[kind])
    if abs(move) <= threshold:
        return OK
    if on is not None and any(abs((on - d).days) <= SPLIT_WINDOW_DAYS for d in action_dates):
        return CheckResult(QualityStatus.OK, f"move {move:.2%} explained by a corporate action")
    return CheckResult(
        QualityStatus.QUARANTINED,
        f"unexplained jump {move:.2%} (limit {threshold:.2%} = max({JUMP_K} x rolling volatility, floor))",
    )


def diff_pct(a: Decimal, b: Decimal) -> Decimal:
    """Relative difference of `a` from reference `b`."""
    return abs(a - b) / b


def disagreement(a: Decimal, b: Decimal, threshold: Decimal) -> tuple[CheckResult, Decimal]:
    d = diff_pct(a, b)
    if d > threshold:
        return CheckResult(QualityStatus.CONFLICTING, f"sources differ by {d:.2%} (limit {threshold:.2%})"), d
    return OK, d
