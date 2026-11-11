"""Lot engine: FIFO consumption with explicit corporate actions (ADR-0006).

Pure function of (effective transactions, corporate actions). Event order within a day:
corporate actions first, then acquisitions, then disposals, then by row id. Lots are
per (account, asset); a MERGER moves lots to the successor asset keeping cost and date.
A BONUS creates a zero-cost lot dated at the ex-date (allotment date approximation).
"""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_DOWN, Decimal

from arthashreni.ledger.types import (
    ACQUIRING,
    DISPOSING,
    ONE,
    ZERO,
    ActionType,
    Consumption,
    CorporateAction,
    LedgerError,
    LotBook,
    LotResult,
    NegativeHoldingError,
    TxnRow,
    q4,
    q8,
)

_Key = tuple[int, int]  # (account_id, asset_id)
_ACTION, _ACQUIRE, _DISPOSE = 0, 1, 2


class _Lot:
    def __init__(
        self,
        lot_id: int,
        txn_id: int | None,
        ca_id: int | None,
        asset_id: int,
        account_id: int,
        acquired_on: date,
        qty: Decimal,
        cost_inr: Decimal,
        cost_orig: Decimal,
        fx_rate_type: str | None,
        is_bonus: bool,
    ) -> None:
        self.id = lot_id
        self.txn_id = txn_id
        self.ca_id = ca_id
        self.adjusted_by: int | None = None
        self.asset_id = asset_id
        self.account_id = account_id
        self.acquired_on = acquired_on
        self.original_qty = qty
        self.qty = qty
        self.cost_inr = cost_inr
        self.cost_orig = cost_orig
        self.rem_cost_inr = cost_inr
        self.fx_rate_type = fx_rate_type
        self.is_bonus = is_bonus


class _Book:
    """All lots ever created (output order) plus the live lots per (account, asset)."""

    def __init__(self) -> None:
        self.all: list[_Lot] = []
        self.active: dict[_Key, list[_Lot]] = {}

    def add(
        self,
        *,
        txn_id: int | None,
        ca_id: int | None,
        asset_id: int,
        account_id: int,
        acquired_on: date,
        qty: Decimal,
        cost_inr: Decimal,
        cost_orig: Decimal,
        fx_rate_type: str | None,
        is_bonus: bool,
    ) -> None:
        lot = _Lot(
            len(self.all) + 1, txn_id, ca_id, asset_id, account_id, acquired_on, qty,
            cost_inr, cost_orig, fx_rate_type, is_bonus,
        )  # fmt: skip
        self.all.append(lot)
        self.active.setdefault((account_id, asset_id), []).append(lot)


def _unit_txn(t: TxnRow) -> tuple[int, Decimal, Decimal]:
    if t.asset_id is None:
        raise LedgerError(f"transaction {t.id}: {t.effective_type} needs an asset")
    if t.quantity is None or t.quantity <= 0:
        raise LedgerError(f"transaction {t.id}: {t.effective_type} needs a positive quantity")
    if t.price is None:
        raise LedgerError(f"transaction {t.id}: {t.effective_type} needs a price")
    return t.asset_id, t.quantity, t.price


def build_lots(
    txns: Sequence[TxnRow], actions: Sequence[CorporateAction], as_of: date | None = None
) -> LotBook:
    events: list[tuple[date, int, int, TxnRow | CorporateAction]] = []
    for a in actions:
        if as_of is None or a.ex_date <= as_of:
            events.append((a.ex_date, _ACTION, a.id, a))
    for t in txns:
        if as_of is not None and t.trade_date > as_of:
            continue
        if t.effective_type in ACQUIRING:
            events.append((t.trade_date, _ACQUIRE, t.id, t))
        elif t.effective_type in DISPOSING:
            events.append((t.trade_date, _DISPOSE, t.id, t))
    events.sort(key=lambda e: e[:3])

    book = _Book()
    consumptions: list[Consumption] = []

    for _, kind, _, ev in events:
        if kind == _ACTION:
            assert isinstance(ev, CorporateAction)
            _apply_action(ev, book)
        elif kind == _ACQUIRE:
            assert isinstance(ev, TxnRow)
            asset_id, qty, price = _unit_txn(ev)
            cost_orig = q4(qty * price + ev.fees + ev.stamp_duty)
            cost_inr = q4(cost_orig * (ev.fx_rate or ONE))
            book.add(
                txn_id=ev.id,
                ca_id=None,
                asset_id=asset_id,
                account_id=ev.account_id,
                acquired_on=ev.trade_date,
                qty=qty,
                cost_inr=cost_inr,
                cost_orig=cost_orig,
                fx_rate_type=ev.fx_rate_type,
                is_bonus=False,
            )
        else:
            assert isinstance(ev, TxnRow)
            _dispose(ev, book.active, consumptions)

    lots = tuple(
        LotResult(
            id=lot.id,
            transaction_id=lot.txn_id,
            corporate_action_id=lot.ca_id,
            adjusted_by_ca_id=lot.adjusted_by,
            asset_id=lot.asset_id,
            account_id=lot.account_id,
            acquired_on=lot.acquired_on,
            original_qty=lot.original_qty,
            remaining_qty=lot.qty,
            cost_inr=lot.cost_inr,
            cost_orig=lot.cost_orig,
            remaining_cost_inr=lot.rem_cost_inr,
            fx_rate_type=lot.fx_rate_type,
            is_bonus=lot.is_bonus,
        )
        for lot in book.all
    )
    return LotBook(lots=lots, consumptions=tuple(consumptions))


def _apply_action(a: CorporateAction, book: _Book) -> None:
    active = book.active
    keys = [k for k in active if k[1] == a.asset_id]
    factor = a.ratio_to / a.ratio_from
    if a.action_type is ActionType.SPLIT:
        for key in keys:
            for lot in active[key]:
                lot.qty = q8(lot.qty * factor)
                lot.adjusted_by = a.id
    elif a.action_type is ActionType.BONUS:
        for key in keys:
            held = sum((lot.qty for lot in active[key]), ZERO)
            bonus = (held * factor).to_integral_value(rounding=ROUND_DOWN)
            if bonus > 0:
                book.add(
                    txn_id=None,
                    ca_id=a.id,
                    asset_id=a.asset_id,
                    account_id=key[0],
                    acquired_on=a.ex_date,
                    qty=bonus,
                    cost_inr=ZERO,
                    cost_orig=ZERO,
                    fx_rate_type=None,
                    is_bonus=True,
                )
    elif a.action_type is ActionType.MERGER:
        if a.successor_asset_id is None:
            raise LedgerError(f"corporate action {a.id}: MERGER needs a successor asset")
        for key in keys:
            moved = active.pop(key)
            for lot in moved:
                lot.qty = q8(lot.qty * factor)
                lot.asset_id = a.successor_asset_id
                lot.adjusted_by = a.id
            target = active.setdefault((key[0], a.successor_asset_id), [])
            target.extend(moved)
            target.sort(key=lambda lot: (lot.acquired_on, lot.id))
    # NAME_CHANGE: no unit or cost change.


def _dispose(t: TxnRow, active: dict[_Key, list[_Lot]], consumptions: list[Consumption]) -> None:
    asset_id, qty, price = _unit_txn(t)
    key = (t.account_id, asset_id)
    lots = active.get(key, [])
    held = sum((lot.qty for lot in lots), ZERO)
    if qty > held:
        raise NegativeHoldingError(t.id, t.account_id, asset_id, qty - held)

    fx = t.fx_rate or ONE
    proceeds_total = q4((qty * price - t.fees - t.stamp_duty) * fx)
    remaining, proceeds_left = qty, proceeds_total
    for lot in lots:
        if remaining == 0:
            break
        take = min(lot.qty, remaining)
        cost = lot.rem_cost_inr if take == lot.qty else q4(lot.rem_cost_inr * take / lot.qty)
        is_last = take == remaining
        proceeds = proceeds_left if is_last else q4(proceeds_total * take / qty)
        consumptions.append(
            Consumption(
                id=len(consumptions) + 1,
                lot_id=lot.id,
                disposal_transaction_id=t.id,
                qty=take,
                cost_inr_consumed=cost,
                proceeds_inr=proceeds,
                holding_days=(t.trade_date - lot.acquired_on).days,
            )
        )
        lot.qty -= take
        lot.rem_cost_inr -= cost
        remaining -= take
        proceeds_left -= proceeds
    active[key] = [lot for lot in lots if lot.qty > 0]
