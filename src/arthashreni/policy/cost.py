"""Cost-efficiency rule (B4). All-in cost of an investment as a percentage of the amount: brokerage, FX markup,
transfer or remittance fees, stamp duty and GST. Above the IPS threshold the money accumulates in the reserve and
is invested less often. No fee is hardcoded: schedules are the investor's inputs (TOML), and a missing schedule
makes the cost unknown, which also means "do not invest this month" rather than a guessed cost."""

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ZERO = Decimal(0)


class CostSchedule(BaseModel):
    model_config = ConfigDict(frozen=True)
    platform: str
    asset_class: str
    brokerage_pct: Decimal = Field(default=ZERO, ge=0)
    brokerage_flat_inr: Decimal = Field(default=ZERO, ge=0)
    fx_markup_pct: Decimal = Field(default=ZERO, ge=0)
    transfer_fee_flat_inr: Decimal = Field(default=ZERO, ge=0)
    stamp_duty_pct: Decimal = Field(default=ZERO, ge=0)
    gst_pct: Decimal = Field(default=ZERO, ge=0)  # on brokerage and transfer fees
    min_ticket_inr: Decimal = Field(default=ZERO, ge=0)


CostBook = dict[tuple[str, str], CostSchedule]


def load_costs(path: Path) -> CostBook:
    """Schedules from a TOML file with `[[schedule]]` tables. A missing file is an empty book."""
    if not path.is_file():
        return {}
    rows = tomllib.loads(path.read_text()).get("schedule", [])
    book = [CostSchedule.model_validate(r) for r in rows]
    return {(s.platform, s.asset_class): s for s in book}


class CostVerdict(StrEnum):
    OK = "ok"
    TOO_COSTLY = "too_costly"
    BELOW_MIN_TICKET = "below_min_ticket"
    UNKNOWN = "unknown"  # no schedule for this platform and instrument


@dataclass(frozen=True)
class CostCheck:
    verdict: CostVerdict
    cost_inr: Decimal | None
    pct: Decimal | None
    note: str


def all_in_cost(amount: Decimal, s: CostSchedule) -> Decimal:
    brokerage = s.brokerage_flat_inr + amount * s.brokerage_pct
    gst = s.gst_pct * (brokerage + s.transfer_fee_flat_inr)
    return brokerage + amount * s.fx_markup_pct + s.transfer_fee_flat_inr + amount * s.stamp_duty_pct + gst


def break_even_amount(s: CostSchedule, threshold: Decimal) -> Decimal | None:
    """Smallest amount whose all-in cost is within `threshold`, or None when percentage costs alone exceed it."""
    flat = s.brokerage_flat_inr + s.transfer_fee_flat_inr
    flat += s.gst_pct * flat
    var = s.brokerage_pct * (1 + s.gst_pct) + s.fx_markup_pct + s.stamp_duty_pct
    if var >= threshold:
        return None
    return flat / (threshold - var) if flat else ZERO


def check_cost(amount: Decimal, s: CostSchedule | None, threshold: Decimal) -> CostCheck:
    if amount <= 0:
        raise ValueError("an investment amount is positive")
    if s is None:
        return CostCheck(CostVerdict.UNKNOWN, None, None, "no cost schedule for this platform and instrument")
    if amount < s.min_ticket_inr:
        return CostCheck(
            CostVerdict.BELOW_MIN_TICKET, None, None, f"below the minimum ticket {s.min_ticket_inr}"
        )
    cost = all_in_cost(amount, s)
    pct = cost / amount
    if pct > threshold:
        be = break_even_amount(s, threshold)
        hint = (
            f"accumulate to at least {be:.2f}"
            if be is not None
            else "percentage costs alone exceed the limit"
        )
        return CostCheck(
            CostVerdict.TOO_COSTLY, cost, pct, f"all-in cost {pct:.2%} exceeds {threshold:.2%}; {hint}"
        )
    return CostCheck(CostVerdict.OK, cost, pct, f"all-in cost {pct:.2%}")
