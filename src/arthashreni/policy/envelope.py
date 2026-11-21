"""Monthly budget envelope (B1, ADR-0009). The Pydantic validator mirrors the database CHECKs, so an invalid
envelope fails before it reaches the database, and the database refuses it if it ever got that far."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

PAISE = Decimal("0.01")
ZERO = Decimal(0)


class OverAllocated(ValueError):
    """Committed SIPs plus new money exceed the month's budget."""


class Kind(StrEnum):
    SIP = "SIP"
    NEW_ALLOCATION = "NEW_ALLOCATION"
    RESERVE_TRANSFER = "RESERVE_TRANSFER"
    RESERVE_USE = "RESERVE_USE"  # spends earlier-set-aside money; outside the month's budget identity (B2)


BUDGET_KINDS = (Kind.SIP, Kind.NEW_ALLOCATION, Kind.RESERVE_TRANSFER)


def is_paise(v: Decimal) -> bool:
    return v == v.quantize(PAISE)


class EnvelopeLine(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Kind
    amount_inr: Decimal
    asset_id: int | None = None
    reserve_id: int | None = None
    sip_mandate_id: int | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.amount_inr <= 0 or not is_paise(self.amount_inr):
            raise ValueError(f"line amount must be positive rupees and paise, got {self.amount_inr}")
        has_asset, has_reserve = self.asset_id is not None, self.reserve_id is not None
        ok = {
            Kind.SIP: has_asset and not has_reserve,
            Kind.NEW_ALLOCATION: has_asset and not has_reserve,
            Kind.RESERVE_TRANSFER: has_reserve and not has_asset,
            Kind.RESERVE_USE: has_reserve and has_asset,
        }[self.kind]
        if not ok:
            raise ValueError(f"{self.kind} line has the wrong asset/reserve fields")
        return self


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True)
    period: date
    opening_budget_inr: Decimal
    committed_sip_inr: Decimal
    new_allocations_inr: Decimal
    reserve_transfers_inr: Decimal
    unallocated_inr: Decimal
    lines: tuple[EnvelopeLine, ...]

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.period.day != 1:
            raise ValueError("a budget period is the first day of a month")
        parts = (
            self.committed_sip_inr,
            self.new_allocations_inr,
            self.reserve_transfers_inr,
            self.unallocated_inr,
        )
        if any(p < 0 for p in parts) or self.opening_budget_inr < 0:
            raise OverAllocated("an envelope component is negative: the budget is over-allocated")
        if sum(parts, ZERO) != self.opening_budget_inr:
            raise OverAllocated(f"envelope does not reconcile: {parts} vs opening {self.opening_budget_inr}")
        by_kind = {k: sum((x.amount_inr for x in self.lines if x.kind is k), ZERO) for k in BUDGET_KINDS}
        if (by_kind[Kind.SIP], by_kind[Kind.NEW_ALLOCATION], by_kind[Kind.RESERVE_TRANSFER]) != parts[:3]:
            raise ValueError("line sums by kind differ from the envelope totals")
        return self


def build_envelope(period: date, opening: Decimal, lines: list[EnvelopeLine]) -> Envelope:
    """Totals come from the lines; whatever is left is `unallocated`. Over-allocation raises `OverAllocated`."""
    total = {k: sum((x.amount_inr for x in lines if x.kind is k), ZERO) for k in BUDGET_KINDS}
    unallocated = opening - sum(total.values(), ZERO)
    if unallocated < 0:
        raise OverAllocated(f"lines add up to {opening - unallocated}, above the budget {opening}")
    return Envelope(
        period=period,
        opening_budget_inr=opening,
        committed_sip_inr=total[Kind.SIP],
        new_allocations_inr=total[Kind.NEW_ALLOCATION],
        reserve_transfers_inr=total[Kind.RESERVE_TRANSFER],
        unallocated_inr=unallocated,
        lines=tuple(lines),
    )


@dataclass(frozen=True)
class Mandate:
    """One SIP mandate row (the latest version of it)."""

    id: int
    asset_id: int
    account_id: int
    amount_inr: Decimal
    day_of_month: int
    platform: str
    start_date: date
    end_date: date | None
    status: str  # ACTIVE | PAUSED | ENDED


def month_end(period: date) -> date:
    return (period.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def active_in(m: Mandate, period: date) -> bool:
    return (
        m.status == "ACTIVE"
        and m.start_date <= month_end(period)
        and (m.end_date is None or m.end_date >= period)
    )


def sip_lines(mandates: list[Mandate], period: date) -> list[EnvelopeLine]:
    """Running SIPs are pre-committed: their amounts leave the budget before the engine sees free money."""
    return [
        EnvelopeLine(kind=Kind.SIP, amount_inr=m.amount_inr, asset_id=m.asset_id, sip_mandate_id=m.id)
        for m in mandates
        if active_in(m, period)
    ]
