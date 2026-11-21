"""Which assets count as equity-oriented and as risk money. One definition for scenarios and policy.

A mutual fund whose category is DEBT is neither. An unknown category (None) or HYBRID is treated as equity-
oriented: overstating risk is the safe error, and the investor can set the category (`asset set`).
"""

EQUITY_CLASSES = frozenset({"EQUITY_IN", "EQUITY_US", "ETF_IN", "ETF_US", "MF"})
RISK_CLASSES = EQUITY_CLASSES | {"SILVER_ETF"}


def is_equity_oriented(asset_class: str, fund_category: str | None = None) -> bool:
    return asset_class in EQUITY_CLASSES and not (asset_class == "MF" and fund_category == "DEBT")


def is_risk_money(asset_class: str, fund_category: str | None = None) -> bool:
    return asset_class in RISK_CLASSES and not (asset_class == "MF" and fund_category == "DEBT")
