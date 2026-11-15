"""Alerts (F2): what happened, when, which assets, evidence, impact, temporary/structural/uncertain, scenarios,
possible responses, the cost of doing nothing and what to watch. Identical alerts are de-duplicated and an
unchanged condition stays silent: the key includes the level, so only a new or escalated condition raises one."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from arthashreni.dq.measured import Measured, m_mul
from arthashreni.risk.signals import Decline, Scope, Signal
from arthashreni.risk.thresholds import Level

MARKET_RESPONSES = (
    "continue the plan",
    "consider rebalancing via contributions",
    "accumulate only if justified",
    "no action",
)
ASSET_RESPONSES = ("review thesis", "hold cash", "reduce only with clear evidence", "no action")
PORTFOLIO_RESPONSES = ("consider rebalancing via contributions", "review thesis", "hold cash", "no action")
CLASS_NOTE = {
    "market-wide": "temporary",  # moves with its benchmark; no evidence of impairment
    "asset-specific": "uncertain",  # diverges from its benchmark, but fundamentals and filings are not available yet
    "uncertain": "uncertain",
}


@dataclass(frozen=True)
class Alert:
    key: str
    level: Level
    what: str
    when: date
    assets: tuple[str, ...]
    evidence: tuple[str, ...]
    sources: tuple[str, ...]
    impact_inr: Measured[Decimal] | None
    classification: str  # temporary | structural | uncertain
    scenarios: tuple[str, ...]
    responses: tuple[str, ...]
    if_nothing: str
    watch: str


@dataclass(frozen=True)
class AlertDiff:
    new: tuple[Alert, ...]
    unchanged: tuple[str, ...]  # keys still true: silent
    cleared: tuple[str, ...]  # open keys whose condition no longer holds


def build_alerts(
    day: date,
    signals: Sequence[Signal],
    declines: Mapping[int, Decline],
    asset_values: Mapping[int, Measured[Decimal]],
    scenario_ids: tuple[str, ...],
    min_level: Level,
) -> list[Alert]:
    out: list[Alert] = []
    for s in signals:
        if s.level is None or s.level < min_level or s.value.value is None:
            continue
        decline = declines.get(s.asset_id) if s.asset_id is not None else None
        classification = CLASS_NOTE[decline.kind] if decline else "uncertain"
        impact = None
        if s.scope is Scope.ASSET and s.asset_id is not None and s.asset_id in asset_values:
            impact = m_mul(
                asset_values[s.asset_id],
                Measured(-s.value.value, s.value.status, s.value.as_of, s.value.source_ids),
            )
        responses = {
            Scope.MARKET: MARKET_RESPONSES,
            Scope.ASSET: ASSET_RESPONSES,
            Scope.PORTFOLIO: PORTFOLIO_RESPONSES,
        }[s.scope]
        why = f" ({decline.kind}: {decline.reason})" if decline else ""
        out.append(
            Alert(
                key=f"{s.id}:{s.level.name}",
                level=s.level,
                what=f"{s.id} is {s.value.value:.2%}: level {s.level.name}{why}",
                when=day,
                assets=(s.subject,) if s.scope is Scope.ASSET else (),
                evidence=(f"signal:{s.id}:{day.isoformat()}",),
                sources=s.value.source_ids,
                impact_inr=impact,
                classification=classification,
                scenarios=scenario_ids,
                responses=responses,
                if_nothing="Nothing is executed automatically; the plan and any SIP continue unchanged until you decide.",
                watch=f"whether {s.id} moves to a different level at the next daily check",
            )
        )
    return out


def diff_alerts(alerts: Sequence[Alert], open_keys: set[str]) -> AlertDiff:
    current = {a.key for a in alerts}
    return AlertDiff(
        tuple(a for a in alerts if a.key not in open_keys),
        tuple(sorted(current & open_keys)),
        tuple(sorted(open_keys - current)),
    )
