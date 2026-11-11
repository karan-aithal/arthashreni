from datetime import date
from decimal import Decimal

import pytest

from arthashreni.ledger.cash import derive_cash
from arthashreni.ledger.effective import resolve_effective
from arthashreni.ledger.fingerprint import fingerprint, with_occurrence
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.reconcile import unit_decimals, unit_diff
from arthashreni.ledger.types import (
    ActionType,
    LedgerError,
    NegativeHoldingError,
    TxnType,
    q4,
    q8,
)
from tests.helpers import D, ca, tx

B, S = TxnType.BUY, TxnType.SELL


def test_fifo_consumes_oldest_lot_first_with_pro_rata_cost() -> None:
    book = build_lots(
        [
            tx(1, B, date(2024, 1, 1), "10", "100", fees="10"),  # cost 1010
            tx(2, B, date(2024, 2, 1), "10", "120"),  # cost 1200
            tx(3, S, date(2024, 3, 1), "15", "150", fees="5"),  # proceeds 2245
        ],
        [],
    )
    first, second = book.consumptions
    assert (first.lot_id, first.qty, first.cost_inr_consumed) == (1, D(10), D("1010.0000"))
    assert (second.lot_id, second.qty, second.cost_inr_consumed) == (2, D(5), D("600.0000"))
    assert first.proceeds_inr + second.proceeds_inr == D("2245.0000")  # remainder keeps the total exact
    assert book.holdings() == {(1, 1): D(5)}
    assert book.lots[1].remaining_cost_inr == D("600.0000")


def test_holding_days_and_fx_cost() -> None:
    book = build_lots(
        [
            tx(1, B, date(2024, 1, 1), "2", "100", fees="1", fx="80"),
            tx(2, S, date(2024, 12, 31), "2", "110", fees="2", fx="85"),
        ],
        [],
    )
    lot = book.lots[0]
    assert lot.cost_orig == D("201.0000") and lot.cost_inr == D("16080.0000")
    assert lot.fx_rate_type == "BROKER_ACTUAL"
    assert book.consumptions[0].holding_days == 365
    assert book.consumptions[0].proceeds_inr == D("18530.0000")  # (220 - 2) * 85


def test_stamp_duty_adds_to_cost_and_reduces_proceeds() -> None:
    book = build_lots(
        [
            tx(1, B, date(2024, 1, 1), "1", "100", stamp="0.5"),
            tx(2, S, date(2024, 1, 2), "1", "100", stamp="0.25"),
        ],
        [],
    )
    assert book.lots[0].cost_inr == D("100.5000")
    assert book.consumptions[0].proceeds_inr == D("99.7500")


def test_same_day_buy_is_processed_before_sell_regardless_of_row_order() -> None:
    book = build_lots([tx(1, S, date(2024, 1, 1), "5", "10"), tx(2, B, date(2024, 1, 1), "5", "9")], [])
    assert book.holdings() == {}


def test_selling_more_than_held_raises_with_details() -> None:
    with pytest.raises(NegativeHoldingError) as exc:
        build_lots(
            [tx(1, B, date(2024, 1, 1), "5", "10"), tx(2, S, date(2024, 1, 2), "6", "10", account=1)], []
        )
    assert (exc.value.txn_id, exc.value.account_id, exc.value.asset_id, exc.value.short) == (2, 1, 1, D(1))
    with pytest.raises(NegativeHoldingError):  # a different account holds nothing
        build_lots(
            [
                tx(1, B, date(2024, 1, 1), "5", "10", account=1),
                tx(2, S, date(2024, 1, 2), "1", "10", account=2),
            ],
            [],
        )


def test_split_scales_units_not_cost() -> None:
    book = build_lots(
        [tx(1, B, date(2024, 1, 1), "10", "100"), tx(2, S, date(2024, 6, 1), "25", "30")],
        [ca(1, ActionType.SPLIT, date(2024, 3, 1), "1", "5")],
    )
    lot = book.lots[0]
    assert lot.original_qty == D(10) and lot.remaining_qty == D(25) and lot.adjusted_by_ca_id == 1
    assert book.consumptions[0].cost_inr_consumed == D("500.0000")  # half the 1000 cost


def test_split_with_fractional_ratio_rounds_to_8dp() -> None:
    book = build_lots(
        [tx(1, B, date(2024, 1, 1), "1", "10")], [ca(1, ActionType.SPLIT, date(2024, 2, 1), "3", "1")]
    )
    assert book.lots[0].remaining_qty == q8(D(1) / 3)


def test_bonus_creates_zero_cost_lot_dated_at_ex_date() -> None:
    book = build_lots(
        [tx(1, B, date(2024, 1, 1), "10", "100"), tx(2, B, date(2024, 1, 2), "5", "100")],
        [ca(1, ActionType.BONUS, date(2024, 2, 1), "2", "1")],  # 1 free for every 2 held -> floor(7.5)
    )
    bonus = book.lots[-1]
    assert bonus.is_bonus and bonus.original_qty == D(7) and bonus.cost_inr == 0
    assert (
        bonus.acquired_on == date(2024, 2, 1)
        and bonus.corporate_action_id == 1
        and bonus.transaction_id is None
    )


def test_bonus_with_nothing_held_creates_no_lot() -> None:
    book = build_lots(
        [tx(1, B, date(2024, 3, 1), "5", "10")], [ca(1, ActionType.BONUS, date(2024, 2, 1), "1", "1")]
    )
    assert len(book.lots) == 1


def test_merger_moves_lots_to_successor_in_fifo_order() -> None:
    book = build_lots(
        [
            tx(1, B, date(2024, 1, 1), "10", "100", asset=1),
            tx(2, B, date(2024, 3, 1), "4", "50", asset=2),  # successor already held
            tx(3, S, date(2024, 6, 1), "21", "10", asset=2),
        ],
        [ca(1, ActionType.MERGER, date(2024, 2, 1), "1", "2", asset=1, successor=2)],
    )
    assert [c.lot_id for c in book.consumptions] == [
        1,
        2,
    ]  # merged lot (older) first, then the successor's own
    assert (
        book.consumptions[0].qty == D(20)
        and book.consumptions[0].holding_days == (date(2024, 6, 1) - date(2024, 1, 1)).days
    )
    assert book.holdings() == {(1, 2): D(3)}
    assert book.lots[0].asset_id == 2 and book.lots[0].original_qty == D(10)


def test_merger_without_successor_and_name_change() -> None:
    with pytest.raises(LedgerError, match="successor"):
        build_lots([tx(1, B, date(2024, 1, 1), "1", "1")], [ca(1, ActionType.MERGER, date(2024, 2, 1))])
    book = build_lots(
        [tx(1, B, date(2024, 1, 1), "3", "1")], [ca(1, ActionType.NAME_CHANGE, date(2024, 2, 1))]
    )
    assert book.holdings() == {(1, 1): D(3)}


def test_as_of_ignores_later_events() -> None:
    txns = [tx(1, B, date(2024, 1, 1), "10", "10"), tx(2, S, date(2024, 6, 1), "10", "10")]
    actions = [ca(1, ActionType.SPLIT, date(2024, 3, 1), "1", "2")]
    assert build_lots(txns, actions, as_of=date(2024, 2, 1)).holdings() == {(1, 1): D(10)}
    assert build_lots(txns, actions, as_of=date(2024, 4, 1)).holdings() == {(1, 1): D(20)}
    assert build_lots(txns, actions, as_of=date(2024, 6, 1)).holdings() == {(1, 1): D(10)}


def test_cash_only_types_do_not_touch_lots() -> None:
    book = build_lots(
        [
            tx(1, TxnType.DIVIDEND, date(2024, 1, 1), asset=1, cash="5"),
            tx(2, TxnType.FEE, date(2024, 1, 1), asset=None, cash="-1"),
        ],
        [],
    )
    assert book.lots == () and book.holdings() == {}


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (tx(1, B, date(2024, 1, 1), "1", "1", asset=None), "needs an asset"),
        (tx(1, B, date(2024, 1, 1), None, "1"), "positive quantity"),
        (tx(1, B, date(2024, 1, 1), "0", "1"), "positive quantity"),
        (tx(1, B, date(2024, 1, 1), "1", None), "needs a price"),
    ],
)
def test_unit_rows_need_asset_quantity_and_price(row, message) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(LedgerError, match=message):
        build_lots([row], [])


def test_corrected_type_is_used_for_corrections() -> None:
    corr = tx(2, TxnType.CORRECTION, date(2024, 1, 1), "4", "10", corrected_type=B, supersedes=1)
    assert corr.effective_type is B
    assert tx(1, B, date(2024, 1, 1), "1", "1").effective_type is B


# ---- effective view ------------------------------------------------------------------------------


def test_effective_drops_superseded_and_voided_rows() -> None:
    rows = [
        tx(1, B, date(2024, 1, 1), "10", "10"),
        tx(2, TxnType.CORRECTION, date(2024, 1, 1), "8", "10", corrected_type=B, supersedes=1),
        tx(3, B, date(2024, 1, 2), "5", "10"),
        tx(4, TxnType.CORRECTION, date(2024, 1, 2), "0", "10", corrected_type=B, supersedes=3),  # void
    ]
    assert [r.id for r in resolve_effective(rows)] == [2]


def test_correction_of_a_correction_keeps_only_the_latest() -> None:
    rows = [
        tx(1, B, date(2024, 1, 1), "10", "10"),
        tx(2, TxnType.CORRECTION, date(2024, 1, 1), "9", "10", corrected_type=B, supersedes=1),
        tx(3, TxnType.CORRECTION, date(2024, 1, 1), "8", "10", corrected_type=B, supersedes=2),
    ]
    assert [r.id for r in resolve_effective(rows)] == [3]


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (tx(2, TxnType.CORRECTION, date(2024, 1, 1), "1", "1", corrected_type=B, supersedes=9), "unknown"),
        (tx(2, TxnType.CORRECTION, date(2024, 1, 1), "1", "1", corrected_type=B, supersedes=2), "earlier"),
    ],
)
def test_effective_rejects_broken_chains(bad, message) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(LedgerError, match=message):
        resolve_effective([tx(1, B, date(2024, 1, 1), "1", "1"), bad])


def test_effective_rejects_two_corrections_of_one_row() -> None:
    rows = [
        tx(1, B, date(2024, 1, 1), "1", "1"),
        tx(2, TxnType.CORRECTION, date(2024, 1, 1), "2", "1", corrected_type=B, supersedes=1),
        tx(3, TxnType.CORRECTION, date(2024, 1, 1), "3", "1", corrected_type=B, supersedes=1),
    ]
    with pytest.raises(LedgerError, match="already corrected"):
        resolve_effective(rows)


# ---- cash, reconcile, fingerprint -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "expected_orig", "expected_inr"),
    [
        (TxnType.BUY, "-1006.5000", "-83539.5000"),
        (TxnType.SIP, "-1006.5000", "-83539.5000"),
        (TxnType.SELL, "993.5000", "82460.5000"),
        (TxnType.REDEMPTION, "993.5000", "82460.5000"),
    ],
)
def test_derive_cash(type_, expected_orig, expected_inr) -> None:  # type: ignore[no-untyped-def]
    orig, inr = derive_cash(type_, D(10), D(100), D("3"), D("2"), D("1.5"), D(83))
    assert (orig, inr) == (D(expected_orig), D(expected_inr))


def test_derive_cash_zero_and_unsupported_type() -> None:
    assert derive_cash(TxnType.BUY, D(0), D(5), D(0), D(0), D(0), None) == (D(0), D(0))
    with pytest.raises(ValueError, match="cannot derive"):
        derive_cash(TxnType.DIVIDEND, D(1), D(1), D(0), D(0), D(0), None)


def test_unit_precision_and_diff() -> None:
    assert unit_decimals("MF") == 3 and unit_decimals("EQUITY_IN") == 6
    assert unit_diff(D("10.0004"), D("10.000"), 3) == 0
    assert unit_diff(D("10.002"), D("10.000"), 3) == D("0.002")
    assert unit_diff(D(1), D(2), 6) == D(-1)


def test_fingerprint_is_stable_and_normalises_values() -> None:
    assert fingerprint("a", D("1.50"), date(2024, 1, 2), None) == fingerprint(
        "A", D("1.5"), date(2024, 1, 2), None
    )
    assert fingerprint("a", 1) != fingerprint("a", 2)


def test_with_occurrence_keeps_identical_rows_distinct_and_is_prefix_stable() -> None:
    keys = [("x", D(1)), ("x", D(1)), ("y", D(2)), ("x", D(1))]
    fps = with_occurrence(keys)
    assert len(set(fps)) == 4
    assert with_occurrence(keys[:2]) == fps[:2]
    assert with_occurrence(keys) == fps


def test_rounding_helpers() -> None:
    assert q4(D("0.00005")) == D("0.0000") and q4(D("0.00015")) == D("0.0002")
    assert q8(D("1.123456785")) == D("1.12345678")
    assert isinstance(q4(Decimal(1)), Decimal)
