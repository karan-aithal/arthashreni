"""Property tests (hypothesis) for ledger invariants: G1.4.

The reference model below is deliberately naive and separate from the engine: it tracks one
running unit balance and a plain list of lots, with events on strictly increasing dates.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arthashreni.ledger.effective import resolve_effective
from arthashreni.ledger.fingerprint import with_occurrence
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.types import ActionType, CorporateAction, TxnRow, TxnType, q4
from tests.helpers import D, tx

pytestmark = pytest.mark.property


@dataclass
class Event:
    kind: str  # buy | sell | split | bonus
    on: date
    qty: int = 0
    price: Decimal = field(default_factory=lambda: D(0))
    fees: Decimal = field(default_factory=lambda: D(0))
    fx: Decimal | None = None
    ratio: int = 1


@st.composite
def histories(draw: st.DrawFn) -> list[Event]:
    events: list[Event] = []
    units = 0
    day = date(2020, 1, 1)
    for _ in range(draw(st.integers(1, 25))):
        day += timedelta(days=draw(st.integers(1, 40)))
        kind = draw(st.sampled_from(["buy", "buy", "sell", "split", "bonus"]))
        if kind == "sell" and units == 0:
            kind = "buy"
        if kind == "buy":
            qty = draw(st.integers(1, 200))
            events.append(
                Event(
                    "buy",
                    day,
                    qty,
                    D(draw(st.integers(1, 50000))) / 100,
                    D(draw(st.integers(0, 500))) / 100,
                    draw(st.sampled_from([None, D("83.1234"), D("91.5")])),
                )
            )
            units += qty
        elif kind == "sell":
            qty = draw(st.integers(1, units))
            events.append(
                Event(
                    "sell",
                    day,
                    qty,
                    D(draw(st.integers(1, 50000))) / 100,
                    D(draw(st.integers(0, 500))) / 100,
                    draw(st.sampled_from([None, D("85")])),
                )
            )
            units -= qty
        elif kind == "split":
            ratio = draw(st.integers(2, 5))
            events.append(Event("split", day, ratio=ratio))
            units *= ratio
        else:
            ratio = draw(st.integers(1, 2))
            events.append(Event("bonus", day, ratio=ratio))
            units += units * ratio
    return events


def to_inputs(events: list[Event]) -> tuple[list[TxnRow], list[CorporateAction]]:
    txns, actions = [], []
    for e in events:
        if e.kind in ("buy", "sell"):
            fx = None if e.fx is None else str(e.fx)
            txns.append(
                tx(
                    len(txns) + 1,
                    TxnType.BUY if e.kind == "buy" else TxnType.SELL,
                    e.on,
                    str(e.qty),
                    str(e.price),
                    fees=str(e.fees),
                    fx=fx,
                )
            )
        else:
            kind = ActionType.SPLIT if e.kind == "split" else ActionType.BONUS
            frm, to = ("1", str(e.ratio))
            actions.append(CorporateAction(len(actions) + 1, 1, kind, e.on, D(frm), D(to)))
    return txns, actions


def reference_units(events: list[Event], upto: int) -> int:
    units = 0
    for e in events[:upto]:
        if e.kind == "buy":
            units += e.qty
        elif e.kind == "sell":
            units -= e.qty
        elif e.kind == "split":
            units *= e.ratio
        else:
            units += units * e.ratio
    return units


@given(histories())
def test_holdings_match_the_running_balance_model(events: list[Event]) -> None:
    txns, actions = to_inputs(events)
    held = build_lots(txns, actions).holdings().get((1, 1), D(0))
    assert held == reference_units(events, len(events))


@given(histories(), st.data())
def test_as_of_matches_the_model_at_any_cut(events: list[Event], data: st.DataObject) -> None:
    cut = data.draw(st.integers(1, len(events)))
    txns, actions = to_inputs(events)
    held = build_lots(txns, actions, as_of=events[cut - 1].on).holdings().get((1, 1), D(0))
    assert held == reference_units(events, cut)


@given(histories())
def test_fifo_matches_a_naive_queue(events: list[Event]) -> None:
    lots: list[list[Decimal]] = []  # [remaining units] in creation order
    expected: list[tuple[int, Decimal]] = []
    for e in events:
        if e.kind == "buy":
            lots.append([D(e.qty)])
        elif e.kind == "split":
            for lot in lots:
                lot[0] *= e.ratio
        elif e.kind == "bonus":
            held = sum((lot[0] for lot in lots), D(0))
            if held > 0:  # nothing held, nothing allotted
                lots.append([held * e.ratio])
        else:
            need = D(e.qty)
            for idx, lot in enumerate(lots):
                if need == 0:
                    break
                take = min(lot[0], need)
                if take > 0:
                    expected.append((idx + 1, take))
                    lot[0] -= take
                    need -= take
    txns, actions = to_inputs(events)
    got = [(c.lot_id, c.qty) for c in build_lots(txns, actions).consumptions]
    assert got == expected


@given(histories())
def test_cost_and_proceeds_are_conserved(events: list[Event]) -> None:
    txns, actions = to_inputs(events)
    book = build_lots(txns, actions)
    total_cost = sum((lot.cost_inr for lot in book.lots), D(0))
    consumed = sum((c.cost_inr_consumed for c in book.consumptions), D(0))
    remaining = sum((lot.remaining_cost_inr for lot in book.lots), D(0))
    assert total_cost == consumed + remaining
    assert all(lot.remaining_cost_inr >= 0 and lot.remaining_qty >= 0 for lot in book.lots)
    assert all((lot.remaining_qty == 0) <= (lot.remaining_cost_inr == 0) for lot in book.lots)

    for t in (t for t in txns if t.type is TxnType.SELL):
        assert t.quantity is not None and t.price is not None
        expected = q4((t.quantity * t.price - t.fees) * (t.fx_rate or D(1)))
        got = sum((c.proceeds_inr for c in book.consumptions if c.disposal_transaction_id == t.id), D(0))
        assert got == expected


@given(histories())
def test_rebuilding_is_deterministic_and_order_independent(events: list[Event]) -> None:
    txns, actions = to_inputs(events)
    first = build_lots(txns, actions)
    assert build_lots(txns, actions) == first
    assert build_lots(list(reversed(txns)), list(reversed(actions))) == first


@given(histories())
def test_bonus_floor_never_creates_fractional_units(events: list[Event]) -> None:
    txns, actions = to_inputs(events)
    book = build_lots(txns, actions)
    for lot in book.lots:
        if lot.is_bonus:
            assert lot.original_qty == lot.original_qty.to_integral_value(rounding=ROUND_DOWN)


@given(st.integers(1, 30), st.data())
def test_effective_view_matches_set_reference(n: int, data: st.DataObject) -> None:
    rows: list[TxnRow] = []
    superseded: set[int] = set()
    voids: set[int] = set()
    for i in range(1, n + 1):
        open_ids = [j for j in range(1, i) if j not in superseded]
        if open_ids and data.draw(st.booleans()):
            target = data.draw(st.sampled_from(open_ids))
            is_void = data.draw(st.booleans())
            rows.append(
                tx(
                    i,
                    TxnType.CORRECTION,
                    date(2024, 1, 1),
                    "0" if is_void else "3",
                    "1",
                    corrected_type=TxnType.BUY,
                    supersedes=target,
                )
            )
            superseded.add(target)
            if is_void:
                voids.add(i)
        else:
            rows.append(tx(i, TxnType.BUY, date(2024, 1, 1), "1", "1"))
    expected = [r.id for r in rows if r.id not in superseded and r.id not in voids]
    assert [r.id for r in resolve_effective(rows)] == expected


@given(st.lists(st.tuples(st.sampled_from("abc"), st.integers(0, 2)), max_size=30))
def test_fingerprints_are_unique_stable_and_prefix_stable(keys: list[tuple[str, int]]) -> None:
    fps = with_occurrence(keys)
    assert len(set(fps)) == len(keys)
    assert with_occurrence(keys) == fps
    half = len(keys) // 2
    assert with_occurrence(keys[:half]) == fps[:half]
