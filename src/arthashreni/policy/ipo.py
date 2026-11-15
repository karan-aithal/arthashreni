"""IPO eligibility (B3). SME IPOs are excluded automatically (minimum application above Rs 2 lakh, V10). For
mainboard IPOs the minimum lot value comes from the RHP and is never hardcoded; "can apply" needs the reserve to
cover it and the research classification to allow it (unrated, the Phase 7 default, does not)."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IpoVerdict:
    can_apply: bool
    reasons: tuple[str, ...]


def ipo_verdict(
    board: str, min_lot_value_inr: Decimal | None, research_class: str, reserve_available_inr: Decimal
) -> IpoVerdict:
    if board != "MAINBOARD":
        return IpoVerdict(False, ("SME and other non-mainboard IPOs are excluded",))
    if min_lot_value_inr is None:
        return IpoVerdict(False, ("minimum lot value is not read from the RHP yet",))
    reasons: list[str] = []
    if reserve_available_inr < min_lot_value_inr:
        reasons.append(
            f"reserve {reserve_available_inr} does not cover the minimum application {min_lot_value_inr}"
        )
    if research_class != "ALLOWED":
        reasons.append(f"research classification is {research_class}, not ALLOWED")
    return IpoVerdict(not reasons, tuple(reasons))
