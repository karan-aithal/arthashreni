"""IPO research checklist (B3, Phase 7).

The system never decides that an IPO is allowed; the investor does, and only after every checklist fact has
been recorded from the RHP with its source. `ALLOWED` without the full checklist, or for a non-mainboard
IPO, is refused."""

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

REQUIRED_FACTS = (
    "min_lot_value",  # INR, the minimum application from the RHP price band (never hardcoded)
    "issue_size",
    "objects_of_issue",
    "promoter_holding",
    "financials_3y",
    "risk_factors_reviewed",
)


def missing_facts(recorded: Iterable[str]) -> tuple[str, ...]:
    have = set(recorded)
    return tuple(f for f in REQUIRED_FACTS if f not in have)


def parse_min_lot_value(value: str) -> Decimal:
    try:
        d = Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"min_lot_value {value!r} is not a number") from None
    if not d.is_finite() or d <= 0:
        raise ValueError("min_lot_value must be a positive number of rupees")
    return d


def classification_refusal(board: str, research_class: str, recorded: Iterable[str]) -> str | None:
    """Why this classification cannot be recorded, or None. NOT_ALLOWED needs nothing; ALLOWED needs all."""
    if research_class not in ("ALLOWED", "NOT_ALLOWED"):
        return f"{research_class!r} is not a classification"
    if research_class == "NOT_ALLOWED":
        return None
    if board != "MAINBOARD":
        return "only mainboard IPOs can be ALLOWED; SME IPOs are excluded"
    gaps = missing_facts(recorded)
    return f"checklist incomplete, missing: {', '.join(gaps)}" if gaps else None
