"""Equity-oriented and risk-money classification (V32): a DEBT mutual fund is neither; unknown counts as equity."""

import pytest

from arthashreni.core.classify import is_equity_oriented, is_risk_money


@pytest.mark.parametrize("cat", [None, "EQUITY", "HYBRID"])
def test_mutual_funds_are_equity_unless_debt(cat: str | None) -> None:
    assert is_equity_oriented("MF", cat) and is_risk_money("MF", cat)


def test_debt_funds_are_neither() -> None:
    assert not is_equity_oriented("MF", "DEBT") and not is_risk_money("MF", "DEBT")


def test_category_only_matters_for_mutual_funds() -> None:
    assert is_equity_oriented("EQUITY_IN", "DEBT") and is_risk_money("ETF_US", "DEBT")
    assert not is_equity_oriented("GOLD_ETF") and not is_risk_money("GOLD_ETF")
    assert is_risk_money("SILVER_ETF") and not is_equity_oriented("SILVER_ETF")
    assert not is_risk_money("CASH") and not is_equity_oriented("SGB")
