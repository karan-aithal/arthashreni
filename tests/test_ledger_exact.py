"""Exact lot-book contents for a scenario with a split, a bonus, a merger and partial sales in two
accounts, plus rounding ties, error text and fingerprints. Written against mutation testing: DB tests
check the same engine, but only exact, DB-free assertions let `mutmut` judge it quickly."""

import hashlib
from datetime import date
from decimal import Decimal

import pytest

from arthashreni.ledger.cash import derive_cash
from arthashreni.ledger.fingerprint import fingerprint, with_occurrence
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.reconcile import quantize_units, unit_decimals, unit_diff
from arthashreni.ledger.types import (
    ActionType,
    Consumption,
    LotBook,
    LotResult,
    NegativeHoldingError,
    TxnType,
    q4,
    q8,
)
from tests.helpers import ca, tx

D = Decimal


def scenario() -> LotBook:
    txns = [
        tx(3, TxnType.SELL, date(2025, 6, 1), "12", "20", asset=1, account=1, fees="2"),
        tx(1, TxnType.BUY, date(2025, 1, 1), "10", "10", asset=1, account=1, fees="1"),
        tx(2, TxnType.BUY, date(2025, 1, 5), "5", "12", asset=1, account=2, stamp="0.5"),
        tx(4, TxnType.BUY, date(2025, 2, 1), "4", "8", asset=1, account=1),
        tx(5, TxnType.BUY, date(2025, 1, 2), "3", "100", asset=2, account=2, fx="80"),
        tx(6, TxnType.SELL, date(2025, 7, 1), "2", "15", asset=3, account=2),
    ]
    actions = [
        ca(1, ActionType.SPLIT, date(2025, 3, 1), "1", "2", asset=1),
        ca(2, ActionType.BONUS, date(2025, 4, 1), "1", "2", asset=1),
        ca(3, ActionType.MERGER, date(2025, 5, 1), "1", "3", asset=2, successor=3),
    ]
    return build_lots(txns, actions)


def lot(**kw: object) -> LotResult:
    base: dict[str, object] = dict(
        transaction_id=None, corporate_action_id=None, adjusted_by_ca_id=None, fx_rate_type=None, is_bonus=False,
    )  # fmt: skip
    return LotResult(**{**base, **kw})  # type: ignore[arg-type]


def test_full_lot_book() -> None:
    """Worked by hand: 10@10+1 fee = 101; 5@12+0.5 stamp = 60.5; 4@8 = 32; split 1:2; bonus 2 free per 1 held
    (acct 1 holds 28 so 56, acct 2 holds 10 so 20); sell 12 of lot 1 (FIFO) = 101 x 12/20 = 60.6 cost and
    20 x 12 - 2 = 238 proceeds; 3 USD lots at 80 = 24000 merge 1:3 into 9 units, sell 2 = 24000 x 2/9."""
    book = scenario()
    assert book.lots == (
        lot(id=1, transaction_id=1, asset_id=1, account_id=1, acquired_on=date(2025, 1, 1), original_qty=D(10),
            remaining_qty=D(8), cost_inr=D(101), cost_orig=D(101), remaining_cost_inr=D("40.4"), adjusted_by_ca_id=1),
        lot(id=2, transaction_id=5, asset_id=3, account_id=2, acquired_on=date(2025, 1, 2), original_qty=D(3),
            remaining_qty=D(7), cost_inr=D(24000), cost_orig=D(300), remaining_cost_inr=D("18666.6667"),
            fx_rate_type="BROKER_ACTUAL", adjusted_by_ca_id=3),
        lot(id=3, transaction_id=2, asset_id=1, account_id=2, acquired_on=date(2025, 1, 5), original_qty=D(5),
            remaining_qty=D(10), cost_inr=D("60.5"), cost_orig=D("60.5"), remaining_cost_inr=D("60.5"),
            adjusted_by_ca_id=1),
        lot(id=4, transaction_id=4, asset_id=1, account_id=1, acquired_on=date(2025, 2, 1), original_qty=D(4),
            remaining_qty=D(8), cost_inr=D(32), cost_orig=D(32), remaining_cost_inr=D(32), adjusted_by_ca_id=1),
        lot(id=5, corporate_action_id=2, asset_id=1, account_id=1, acquired_on=date(2025, 4, 1),
            original_qty=D(56), remaining_qty=D(56), cost_inr=D(0), cost_orig=D(0), remaining_cost_inr=D(0),
            is_bonus=True),
        lot(id=6, corporate_action_id=2, asset_id=1, account_id=2, acquired_on=date(2025, 4, 1),
            original_qty=D(20), remaining_qty=D(20), cost_inr=D(0), cost_orig=D(0), remaining_cost_inr=D(0),
            is_bonus=True),
    )  # fmt: skip
    assert book.consumptions == (
        Consumption(1, 1, 3, D(12), D("60.6"), D(238), 151),
        Consumption(2, 2, 6, D(2), D("5333.3333"), D(30), 180),
    )
    assert book.holdings() == {(1, 1): D(72), (2, 1): D(30), (2, 3): D(7)}


def test_events_after_as_of_are_skipped_even_when_listed_first() -> None:
    txns = [
        tx(2, TxnType.BUY, date(2025, 3, 1), "5", "10"),  # listed first but after the cut-off
        tx(1, TxnType.BUY, date(2025, 1, 1), "7", "10"),
        tx(3, TxnType.SELL, date(2025, 3, 5), "1", "10"),
    ]
    book = build_lots(txns, [], as_of=date(2025, 2, 1))
    assert [lot_.original_qty for lot_ in book.lots] == [D(7)] and book.consumptions == ()
    split = [
        ca(1, ActionType.SPLIT, date(2025, 4, 1), "1", "2"),
        ca(2, ActionType.SPLIT, date(2025, 1, 15), "1", "3"),
    ]
    assert build_lots(txns[1:2], split, as_of=date(2025, 2, 1)).lots[0].remaining_qty == D(21)
    assert build_lots(txns[1:2], split).lots[0].remaining_qty == D(42)


def test_bonus_rounds_down_and_split_ratio_rounds_to_eight_places() -> None:
    txns = [tx(1, TxnType.BUY, date(2025, 1, 1), "5", "10")]
    book = build_lots(
        txns, [ca(1, ActionType.BONUS, date(2025, 2, 1), "2", "1")]
    )  # 1 free per 2 held: 2.5 -> 2
    assert [lot_.original_qty for lot_ in book.lots] == [D(5), D(2)] and book.lots[1].is_bonus
    thirds = build_lots(txns, [ca(1, ActionType.SPLIT, date(2025, 2, 1), "3", "1")])
    assert str(thirds.lots[0].remaining_qty) == "1.66666667"
    assert build_lots(txns, [ca(1, ActionType.BONUS, date(2025, 2, 1), "10", "1")]).lots[
        0
    ].remaining_qty == D(5)


def test_partial_lot_consumption_splits_cost_and_proceeds_pro_rata() -> None:
    txns = [
        tx(1, TxnType.BUY, date(2025, 1, 1), "3", "10"),
        tx(2, TxnType.BUY, date(2025, 1, 2), "3", "20"),
        tx(3, TxnType.SELL, date(2025, 2, 1), "4", "30", fees="1"),
    ]
    book = build_lots(txns, [])
    first, second = book.consumptions
    # proceeds 4 x 30 - 1 = 119; first lot 3/4 of it, the last lot takes the remainder so totals are exact
    assert (first.qty, first.cost_inr_consumed, first.proceeds_inr) == (D(3), D(30), D("89.25"))
    assert (second.qty, second.cost_inr_consumed, second.proceeds_inr) == (D(1), D(20), D("29.75"))
    assert (first.id, second.id) == (1, 2) and first.proceeds_inr + second.proceeds_inr == D(119)
    assert book.lots[1].remaining_cost_inr == D(40) and book.lots[1].remaining_qty == D(2)


def test_negative_holding_error_text() -> None:
    txns = [tx(1, TxnType.BUY, date(2025, 1, 1), "1", "10"), tx(2, TxnType.SELL, date(2025, 1, 2), "3", "10")]
    with pytest.raises(NegativeHoldingError) as err:
        build_lots(txns, [])
    assert str(err.value) == "transaction 2 would make holding negative (account 1, asset 1, short by 2)"
    assert (err.value.txn_id, err.value.account_id, err.value.asset_id, err.value.short) == (2, 1, 1, D(2))


def test_rounding_is_half_even_at_ties() -> None:
    assert q4(D("0.00005")) == D("0.0000") and q4(D("0.00015")) == D("0.0002")
    assert q4(D("-0.00005")) == D("0.0000") and str(q4(D("1"))) == "1.0000"
    assert q8(D("0.000000005")) == D("0.00000000") and q8(D("0.000000015")) == D("0.00000002")
    assert str(q8(D("1"))) == "1.00000000"
    assert quantize_units(D("0.0005"), 3) == D("0.000") and quantize_units(D("0.0015"), 3) == D("0.002")
    assert str(quantize_units(D("1"), 3)) == "1.000" and str(quantize_units(D("1"), 6)) == "1.000000"
    assert unit_decimals("MF") == 3 and unit_decimals("ETF_IN") == 6
    assert unit_diff(D("10.0004"), D("10.0001"), 3) == D(0) and unit_diff(D("10.0006"), D("10"), 3) == D(
        "0.001"
    )
    assert unit_diff(D("1"), D("2"), 3) == D(-1)


def test_derive_cash_exact() -> None:
    d = derive_cash
    assert d(TxnType.BUY, D(2), D(10), D(1), D("0.5"), D("0.25"), None) == (D("-21.75"), D("-21.75"))
    assert d(TxnType.SIP, D(2), D(10), D(0), D(0), D(0), D(80)) == (D(-20), D(-1600))
    assert d(TxnType.SELL, D(2), D(10), D(1), D("0.5"), D("0.25"), D(80)) == (D("18.25"), D(1460))
    assert d(TxnType.REDEMPTION, D(2), D(10), D(0), D(0), D(0), None) == (D(20), D(20))
    zero = d(TxnType.SELL, D(1), D(10), D(10), D(0), D(0), D(80))
    assert zero == (D(0), D(0)) and str(zero[1]) == "0"
    with pytest.raises(ValueError, match=r"cannot derive net cash for (TxnType\.)?DIVIDEND"):
        d(TxnType.DIVIDEND, D(1), D(1), D(0), D(0), D(0), None)


def test_fingerprints_are_stable_normalised_and_exact() -> None:
    parts = ["a", 1, D("1.50"), date(2025, 1, 2), None, " x "]
    expected = hashlib.sha256("\x1f".join(["A", "1", "1.5", "2025-01-02", "", "X"]).encode()).hexdigest()
    assert fingerprint(*parts) == expected
    assert fingerprint(D("100")) == hashlib.sha256(b"100").hexdigest()  # normalised without exponent notation
    assert fingerprint(D("1E+2")) == fingerprint(D("100"))
    assert fingerprint("x", None) != fingerprint("x") and fingerprint("a", "b") != fingerprint("ab")
    assert fingerprint(None) == hashlib.sha256(b"").hexdigest()
    keys = [("a", D("1.0")), ("A", D("1")), ("b",), ("a", D("1.00"))]
    assert with_occurrence(keys) == [fingerprint("a", D("1.0"), 1), fingerprint("A", D("1"), 2), fingerprint("b", 1),
                                     fingerprint("a", D("1.00"), 3)]  # fmt: skip
    assert with_occurrence([]) == []
    assert with_occurrence([("x",)] * 2)[0] != with_occurrence([("x",)] * 2)[1]
