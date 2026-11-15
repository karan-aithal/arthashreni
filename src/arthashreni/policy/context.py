"""Personal financial context and the hard liquidity rules (B6). Each field is known, assumed or unknown.

Rules run before any scoring:
- emergency fund below the IPS minimum: build liquidity first; unknown: insufficient evidence (no new money);
- a large planned expense inside the IPS window: no new risk money;
- income stability unknown or unstable: the risk level of the mix may not rise (unknown is treated as unstable).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from arthashreni.policy.ips import IPS

FIELDS = (
    "take_home_income_inr",
    "income_stable",
    "essential_expenses_inr",
    "loans_emi_inr",
    "emergency_fund_inr",
    "planned_expenses",
    "cash_reserves_inr",
    "goals",
    "planned_withdrawals",
)


# Classes that count as risk money. Mutual funds are assumed equity-oriented because no scheme category is stored (V32).
RISKY_CLASSES = frozenset({"EQUITY_IN", "EQUITY_US", "ETF_IN", "ETF_US", "MF", "SILVER_ETF"})


class Certainty(StrEnum):
    KNOWN = "KNOWN"
    ASSUMED = "ASSUMED"
    UNKNOWN = "UNKNOWN"


class ContextField(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Any = None
    certainty: Certainty = Certainty.UNKNOWN


Context = dict[str, ContextField]


class Verdict(StrEnum):
    OK = "ok"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Liquidity:
    emergency: Verdict
    emergency_shortfall_inr: Decimal  # rupees to add to reach the IPS minimum (0 unless emergency is FAIL)
    expense_window: Verdict  # FAIL: a large expense falls inside the window
    income_unstable: bool
    notes: tuple[str, ...] = field(default=())
    assumptions: tuple[str, ...] = field(default=())


def _get(ctx: Context, name: str) -> ContextField:
    return ctx.get(name) or ContextField()


def _num(ctx: Context, name: str) -> Decimal | None:
    f = _get(ctx, name)
    if f.certainty is Certainty.UNKNOWN or f.value is None:
        return None
    return Decimal(str(f.value))


def liquidity(ctx: Context, ips: IPS, today: date) -> Liquidity:
    notes: list[str] = []
    assumed = [n for n in FIELDS if _get(ctx, n).certainty is Certainty.ASSUMED]
    fund, expenses = _num(ctx, "emergency_fund_inr"), _num(ctx, "essential_expenses_inr")
    shortfall = Decimal(0)
    if fund is None or expenses is None:
        emergency = Verdict.UNKNOWN
        notes.append("emergency fund or essential expenses unknown: liquidity cannot be confirmed")
    else:
        shortfall = max(Decimal(0), ips.emergency_fund_min_months * expenses - fund)
        emergency = Verdict.FAIL if shortfall > 0 else Verdict.OK
        if shortfall > 0:
            notes.append(
                f"emergency fund is {shortfall:.2f} below {ips.emergency_fund_min_months} months of expenses"
            )
    planned = _get(ctx, "planned_expenses")
    window_end = today + timedelta(days=ips.planned_expense_window_days)
    expense = Verdict.UNKNOWN
    if planned.certainty is not Certainty.UNKNOWN:
        due = [e for e in (planned.value or []) if today <= date.fromisoformat(str(e["date"])) <= window_end]
        expense = Verdict.FAIL if due else Verdict.OK
        if due:
            notes.append(f"{len(due)} large expense(s) fall within {ips.planned_expense_window_days} days")
    else:
        notes.append("planned large expenses unknown: not applied")
    stable = _get(ctx, "income_stable")
    unstable = stable.certainty is Certainty.UNKNOWN or stable.value is not True
    if unstable:
        notes.append("income not confirmed stable: the risk level of the mix may not rise")
    return Liquidity(
        emergency,
        shortfall,
        expense,
        unstable,
        tuple(notes),
        tuple(f"{n} is an assumption" for n in assumed),
    )
