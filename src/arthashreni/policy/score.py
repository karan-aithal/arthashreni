"""Advisory score (F3.4): market conditions, asset quality, portfolio fit, valuation/timing, personal context.
Advisory only: it orders candidates that already passed every gate; it can never override a gate, veto, band or
the budget. A component with no data is `None`, contributes nothing, and the remaining weights are renormalised, so
a missing input is never replaced by an invented number."""

from dataclasses import dataclass
from decimal import Decimal

from arthashreni.policy.ips import AdvisoryWeights

ZERO = Decimal(0)


@dataclass(frozen=True)
class Score:
    total: Decimal | None  # 0..1; None when no component had data
    components: dict[str, Decimal | None]
    note: str


def advisory_score(components: dict[str, Decimal | None], weights: AdvisoryWeights) -> Score:
    w = weights.model_dump()
    unknown = set(components) - set(w)
    if unknown:
        raise ValueError(f"unknown score components: {sorted(unknown)}")
    used = {k: v for k, v in components.items() if v is not None}
    if any(not ZERO <= v <= 1 for v in used.values()):
        raise ValueError("score components are between 0 and 1")
    weight_used = sum((w[k] for k in used), ZERO)
    if weight_used == 0:
        return Score(None, {k: components.get(k) for k in w}, "no score component has data")
    total = sum((w[k] * v for k, v in used.items()), ZERO) / weight_used
    missing = sorted(set(w) - set(used))
    note = f"advisory; {', '.join(missing)} had no data" if missing else "advisory"
    return Score(total, {k: components.get(k) for k in w}, note)
