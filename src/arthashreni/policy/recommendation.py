"""The recommendation (F3): a versioned JSON schema. Validators repeat the budget rules, so a recommendation that
over-allocates, claims an envelope line twice, allocates without an approved IPS or omits the approval flag cannot
be constructed."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from arthashreni.policy.envelope import Envelope, Kind

SCHEMA_VERSION = "1.0"


class Action(StrEnum):
    CONTINUE_SIP = "continue_sip"
    ACCUMULATE = "accumulate"
    REDUCE = "reduce"
    REVIEW = "review"
    HOLD = "hold"
    AVOID = "avoid"
    HOLD_CASH = "hold_cash"
    NO_ACTION = "no_action"


class RecLine(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: Action
    amount_inr: Decimal
    subject: str  # asset name, asset class or reserve name
    asset_id: int | None = None
    envelope_ref: int | None = None  # index into envelope.lines; exactly one per rupee-bearing line
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    data_freshness: str = "ok"
    confidence: str = "low"
    score: dict[str, Any] | None = None


class Recommendation(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    review_date: date
    period: date
    dataset: Literal["DEMO", "REAL"]
    label: str
    personalised: bool  # False: no approved IPS, so nothing beyond committed SIPs is allocated
    risk_level: str
    overall_action: Action
    budget_inr: Decimal
    envelope: Envelope
    lines: tuple[RecLine, ...]
    reserve_movements: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    scenarios: tuple[dict[str, Any], ...] = ()
    sources: tuple[str, ...] = ()
    confidence: str
    confidence_reasons: tuple[str, ...] = ()
    ips_version: int | None = None
    human_approval_required: Literal[True] = True

    @model_validator(mode="after")
    def _rules(self) -> Self:
        env = self.envelope
        if env.opening_budget_inr != self.budget_inr:
            raise ValueError("budget differs from the envelope's opening budget")
        if self.dataset == "DEMO" and not self.label.startswith("DEMO"):
            raise ValueError("DEMO output must be labelled DEMO")
        if not self.personalised and (env.new_allocations_inr or env.reserve_transfers_inr):
            raise ValueError("no approved IPS: only committed SIPs may be recommended")
        refs = [ln.envelope_ref for ln in self.lines if ln.envelope_ref is not None]
        if len(refs) != len(set(refs)):
            raise ValueError("an envelope line is claimed by two recommendation lines")
        if set(refs) != set(range(len(env.lines))):
            raise ValueError("every envelope line needs exactly one recommendation line")
        for ln in self.lines:
            if ln.envelope_ref is None:
                if ln.amount_inr != 0:
                    raise ValueError("a line with money needs an envelope line")
                continue
            e = env.lines[ln.envelope_ref]
            if e.amount_inr != ln.amount_inr:
                raise ValueError("line amount differs from its envelope line")
            if (e.kind is Kind.SIP) != (ln.action is Action.CONTINUE_SIP):
                raise ValueError("only SIP envelope lines carry continue_sip")
        return self
