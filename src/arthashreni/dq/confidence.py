"""Computed confidence (E5, ADR-0010). A deterministic function of input statuses; never an LLM judgement.

coverage  = value share whose inputs are `ok`
freshness = value share not `stale` or `missing`
agreement = share (by value) of cross-checked inputs without `conflicting`; nothing cross-checked gives 0
High needs all three >= HIGH_AT, Medium all three >= MEDIUM_AT, otherwise Low; any `quarantined` input is Low.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from arthashreni.dq.measured import QualityStatus

HIGH_AT = Decimal("0.95")
MEDIUM_AT = Decimal("0.80")
ZERO, ONE = Decimal(0), Decimal(1)


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ConfidenceInput:
    weight: Decimal  # INR value (cost when the value is unknown); only ratios matter
    status: QualityStatus
    cross_checked: bool = False  # a second source or a statement price was compared with this value


@dataclass(frozen=True)
class Confidence:
    level: ConfidenceLevel
    coverage: Decimal
    freshness: Decimal
    agreement: Decimal
    reasons: tuple[str, ...]


def _share(part: Decimal, whole: Decimal) -> Decimal:
    return part / whole if whole > 0 else ZERO


def confidence(
    inputs: Sequence[ConfidenceInput], high_at: Decimal = HIGH_AT, medium_at: Decimal = MEDIUM_AT
) -> Confidence:
    if not inputs:
        return Confidence(ConfidenceLevel.LOW, ZERO, ZERO, ZERO, ("no inputs",))
    if any(i.weight < 0 for i in inputs):
        raise ValueError("weights are values and cannot be negative")
    if sum((i.weight for i in inputs), ZERO) == 0:
        inputs = [ConfidenceInput(ONE, i.status, i.cross_checked) for i in inputs]  # all unvalued: count them
    total = sum((i.weight for i in inputs), ZERO)
    ok = sum((i.weight for i in inputs if i.status is QualityStatus.OK), ZERO)
    fresh = sum(
        (i.weight for i in inputs if i.status not in (QualityStatus.STALE, QualityStatus.MISSING)), ZERO
    )
    checked = [i for i in inputs if i.cross_checked or i.status is QualityStatus.CONFLICTING]
    checked_total = sum((i.weight for i in checked), ZERO)
    agreed = sum((i.weight for i in checked if i.status is not QualityStatus.CONFLICTING), ZERO)
    coverage, freshness, agreement = _share(ok, total), _share(fresh, total), _share(agreed, checked_total)
    reasons: list[str] = []
    if not checked:
        reasons.append("nothing could be cross-checked, so agreement is 0")
    quarantined = any(i.status is QualityStatus.QUARANTINED for i in inputs)
    if quarantined:
        reasons.append("a quarantined input forces low confidence")
    weakest = min(coverage, freshness, agreement)
    if quarantined or weakest < medium_at:
        level = ConfidenceLevel.LOW
    elif weakest >= high_at:
        level = ConfidenceLevel.HIGH
    else:
        level = ConfidenceLevel.MEDIUM
    for name, v in (("coverage", coverage), ("freshness", freshness), ("agreement", agreement)):
        if v < high_at:
            reasons.append(f"{name} {v:.0%} is below {high_at:.0%}")
    return Confidence(level, coverage, freshness, agreement, tuple(reasons))
