"""Portfolio risk level from signals (F2) and vetoes for the decision engine (F3.3).

The level is a count rule over signals (thresholds config); missing signals do not count and are listed, and the
result says how many signals it rests on. A level never triggers a trade (invariant 12): it only feeds the advisory
score and the report, and the asset-specific veto only stops *new* money."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from arthashreni.risk.signals import Decline, Scope, Signal
from arthashreni.risk.thresholds import Level, Rules


@dataclass(frozen=True)
class LevelResult:
    level: Level
    at_or_above: dict[str, int]  # "caution" | "high" | "severe" -> number of signals
    available: int
    total: int
    drivers: tuple[str, ...]  # ids of signals at CAUTION or worse
    missing: tuple[str, ...]  # ids with no value


# Signals of these kinds share one root cause (an overweight class is also an overweight country and currency), so
# only the worst of each kind counts toward the level; all of them are still shown and stored.
COLLAPSED_KINDS = frozenset({"band_breach"})


def overall_level(signals: Sequence[Signal], rules: Rules) -> LevelResult:
    levelled = [s for s in signals if s.level is not None]
    for kind in COLLAPSED_KINDS:
        group = [s for s in levelled if s.kind == kind]
        if len(group) > 1:
            worst_one = max(group, key=lambda s: (s.level or Level.NORMAL, s.id))
            levelled = [s for s in levelled if s.kind != kind or s is worst_one]
    n = {
        "caution": sum(s.level >= Level.CAUTION for s in levelled if s.level is not None),
        "high": sum(s.level >= Level.HIGH_RISK for s in levelled if s.level is not None),
        "severe": sum(s.level >= Level.SEVERE_STRESS for s in levelled if s.level is not None),
    }
    if n["severe"] >= rules.severe_stress_if_severe:
        level = Level.SEVERE_STRESS
    elif n["severe"] >= rules.high_risk_if_severe or n["high"] >= rules.high_risk_if_high:
        level = Level.HIGH_RISK
    elif n["high"] >= rules.caution_if_high or n["caution"] >= rules.caution_if_caution:
        level = Level.CAUTION
    else:
        level = Level.NORMAL
    return LevelResult(
        level,
        n,
        len(levelled),
        len(signals),
        tuple(s.id for s in levelled if s.level is not None and s.level >= Level.CAUTION),
        tuple(s.id for s in signals if s.level is None),
    )


def vetoes(signals: Sequence[Signal], declines: Mapping[int, Decline]) -> dict[int, tuple[str, ...]]:
    """Assets with a severe asset-specific signal get no new money, whatever their score (F3.3). A severe fall
    that is market-wide or uncertain is not a veto: it is shown, and the investor reviews it."""
    out: dict[int, tuple[str, ...]] = {}
    for s in signals:
        if s.scope is Scope.ASSET and s.asset_id is not None and s.level is Level.SEVERE_STRESS:
            d = declines.get(s.asset_id)
            if d is not None and d.kind == "asset-specific":
                out[s.asset_id] = (
                    *out.get(s.asset_id, ()),
                    f"severe asset-specific decline ({s.id}): {d.reason}",
                )
    return out
