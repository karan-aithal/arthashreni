"""SQL access for the ledger. Tables resolve through the session's search_path (demo or real)."""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.ledger.fingerprint import fingerprint
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.models import NewCorporateAction, NewStatementHolding, NewTxn
from arthashreni.ledger.types import (
    ActionType,
    CorporateAction,
    LedgerError,
    LotBook,
    TxnRow,
    TxnType,
)

_EFFECTIVE_COLS = (
    "id, asset_id, account_id, effective_type, trade_date, quantity, price, currency, fx_rate, "
    "fx_rate_type, fees, taxes, stamp_duty, net_cash_inr, net_cash_orig, supersedes_id"
)


def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# ---- accounts ------------------------------------------------------------------------------


def add_account(
    db: Session,
    *,
    platform: str,
    account_kind: str,
    label: str,
    currency: str = "INR",
    folio_or_ref_masked: str | None = None,
    opened_on: date | None = None,
) -> int:
    return int(
        db.execute(
            text(
                """INSERT INTO accounts
                       (platform, account_kind, label, currency, folio_or_ref_masked, opened_on)
                   VALUES (:p, :k, :l, :c, :m, :o) RETURNING id"""
            ),
            {
                "p": platform,
                "k": account_kind,
                "l": label,
                "c": currency.upper(),
                "m": folio_or_ref_masked,
                "o": opened_on,
            },
        ).scalar_one()
    )


def account_ids(db: Session) -> dict[str, int]:
    return {str(r[0]): int(r[1]) for r in db.execute(text("SELECT label, id FROM accounts"))}


# ---- transactions --------------------------------------------------------------------------

_INSERT_TXN = text(
    """INSERT INTO transactions (asset_id, account_id, txn_type, corrected_type, trade_date, settlement_date,
        price_date, quantity, price, currency, fx_rate, fx_rate_type, fees, taxes, stamp_duty, net_cash_inr,
        net_cash_orig, source_document, import_batch_id, raw_record_id, verification_status, supersedes_id,
        correction_reason, fingerprint, source_tz)
       VALUES (:asset_id, :account_id, :txn_type, :corrected_type, :trade_date, :settlement_date, :price_date,
        :quantity, :price, :currency, :fx_rate, :fx_rate_type, :fees, :taxes, :stamp_duty, :net_cash_inr,
        :net_cash_orig, :source_document, :import_batch_id, :raw_record_id, :verification_status,
        :supersedes_id, :correction_reason, :fingerprint, :source_tz)
       ON CONFLICT (fingerprint) DO NOTHING RETURNING id"""
)


def _insert_txn(
    db: Session,
    n: NewTxn,
    *,
    fp: str,
    txn_type: TxnType,
    corrected_type: TxnType | None = None,
    supersedes_id: int | None = None,
    correction_reason: str | None = None,
    import_batch_id: int | None = None,
    raw_record_id: int | None = None,
) -> int | None:
    params = n.model_dump()
    params.update(
        txn_type=txn_type.value,
        corrected_type=corrected_type.value if corrected_type else None,
        supersedes_id=supersedes_id,
        correction_reason=correction_reason,
        fingerprint=fp,
        import_batch_id=import_batch_id,
        raw_record_id=raw_record_id,
    )
    params.pop("type")
    row = db.execute(_INSERT_TXN, params).first()
    return None if row is None else int(row[0])


def insert_transaction(
    db: Session, n: NewTxn, *, fp: str, import_batch_id: int | None = None, raw_record_id: int | None = None
) -> int | None:
    """Append a transaction. Returns None if its fingerprint already exists (duplicate = no-op)."""
    return _insert_txn(
        db, n, fp=fp, txn_type=n.type, import_batch_id=import_batch_id, raw_record_id=raw_record_id
    )


def _physical_row(db: Session, txn_id: int) -> dict[str, Any]:
    row = (
        db.execute(
            text(
                """SELECT asset_id, account_id, COALESCE(corrected_type, txn_type) AS type, trade_date,
                      settlement_date, price_date, quantity, price, currency, fx_rate, fx_rate_type, fees,
                      taxes, stamp_duty, net_cash_inr, net_cash_orig, source_document, verification_status,
                      source_tz,
                      EXISTS (SELECT 1 FROM transactions c WHERE c.supersedes_id = t.id) AS corrected
               FROM transactions t WHERE id = :id"""
            ),
            {"id": txn_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LedgerError(f"transaction {txn_id} does not exist")
    return dict(row)


def correct_transaction(db: Session, txn_id: int, reason: str, changes: dict[str, Any]) -> int:
    """Append a CORRECTION that supersedes `txn_id` with the original values plus `changes`."""
    return _correct(db, txn_id, reason, changes, validate=True)


def void_transaction(db: Session, txn_id: int, reason: str) -> int:
    """Cancel a transaction: a correction with zero quantity and zero cash."""
    orig = _physical_row(db, txn_id)
    changes: dict[str, Any] = {
        "quantity": Decimal(0) if orig["quantity"] is not None else None,
        "fees": Decimal(0),
        "taxes": Decimal(0),
        "stamp_duty": Decimal(0),
        "net_cash_inr": Decimal(0),
        "net_cash_orig": Decimal(0),
    }
    # A void deliberately has zero quantity, which NewTxn would refuse for unit types.
    return _correct(db, txn_id, reason, changes, validate=False)


def _correct(db: Session, txn_id: int, reason: str, changes: dict[str, Any], *, validate: bool) -> int:
    if not reason.strip():
        raise LedgerError("a correction needs a reason")
    orig = _physical_row(db, txn_id)
    if orig.pop("corrected"):
        raise LedgerError(f"transaction {txn_id} is already corrected; correct the correction")
    merged = {**orig, **changes}
    new = NewTxn.model_validate(merged) if validate else NewTxn.model_construct(**merged)
    fp = fingerprint("CORRECTION", txn_id, reason, *sorted((k, str(v)) for k, v in merged.items()))
    new_id = _insert_txn(
        db,
        new,
        fp=fp,
        txn_type=TxnType.CORRECTION,
        corrected_type=TxnType(merged["type"]),
        supersedes_id=txn_id,
        correction_reason=reason.strip(),
    )
    if new_id is None:
        raise LedgerError("this exact correction was already recorded")
    return new_id


def _txn_rows(rows: Any, *, audit: bool) -> list[TxnRow]:
    return [
        TxnRow(
            id=int(r[0]),
            asset_id=r[1],
            account_id=int(r[2]),
            type=TxnType(r[3]),
            trade_date=r[4],
            quantity=_dec(r[5]),
            price=_dec(r[6]),
            currency=r[7],
            fx_rate=_dec(r[8]),
            fx_rate_type=r[9],
            fees=Decimal(str(r[10])),
            taxes=Decimal(str(r[11])),
            stamp_duty=Decimal(str(r[12])),
            net_cash_inr=Decimal(str(r[13])),
            net_cash_orig=Decimal(str(r[14])),
            supersedes_id=r[15],
            corrected_type=TxnType(r[16]) if audit and r[16] else None,
        )
        for r in rows
    ]


def load_effective(db: Session) -> list[TxnRow]:
    """The reporting view: corrections applied, superseded rows and voids removed."""
    rows = db.execute(text(f"SELECT {_EFFECTIVE_COLS}, NULL FROM transactions_effective ORDER BY id")).all()  # noqa: S608
    return _txn_rows(rows, audit=False)


def load_audit(db: Session) -> list[TxnRow]:
    """The audit view: every physical row, corrections and superseded rows included."""
    cols = _EFFECTIVE_COLS.replace("effective_type", "txn_type")
    rows = db.execute(text(f"SELECT {cols}, corrected_type FROM transactions ORDER BY id")).all()  # noqa: S608
    return _txn_rows(rows, audit=True)


# ---- corporate actions ---------------------------------------------------------------------


def insert_corporate_action(
    db: Session, a: NewCorporateAction, *, fp: str, import_batch_id: int | None = None
) -> int | None:
    row = db.execute(
        text(
            """INSERT INTO corporate_actions (asset_id, action_type, ex_date, ratio_from, ratio_to,
                   successor_asset_id, source_document, import_batch_id, fingerprint)
               VALUES (:asset_id, :action_type, :ex_date, :ratio_from, :ratio_to, :successor_asset_id,
                   :source_document, :batch, :fp)
               ON CONFLICT (fingerprint) DO NOTHING RETURNING id"""
        ),
        {
            **a.model_dump(mode="python"),
            "action_type": a.action_type.value,
            "batch": import_batch_id,
            "fp": fp,
        },
    ).first()
    return None if row is None else int(row[0])


def load_actions(db: Session) -> list[CorporateAction]:
    rows = db.execute(
        text(
            """SELECT id, asset_id, action_type, ex_date, ratio_from, ratio_to, successor_asset_id
               FROM corporate_actions ORDER BY ex_date, id"""
        )
    ).all()
    return [
        CorporateAction(
            id=int(r[0]),
            asset_id=int(r[1]),
            action_type=ActionType(r[2]),
            ex_date=r[3],
            ratio_from=Decimal(str(r[4])),
            ratio_to=Decimal(str(r[5])),
            successor_asset_id=r[6],
        )
        for r in rows
    ]


# ---- statement holdings --------------------------------------------------------------------


def insert_statement_holding(
    db: Session, h: NewStatementHolding, *, fp: str, import_batch_id: int
) -> int | None:
    row = db.execute(
        text(
            """INSERT INTO statement_holdings (import_batch_id, account_id, asset_id, as_of, units,
                   source_document, fingerprint)
               VALUES (:batch, :account_id, :asset_id, :as_of, :units, :source_document, :fp)
               ON CONFLICT (fingerprint) DO NOTHING RETURNING id"""
        ),
        {**h.model_dump(), "batch": import_batch_id, "fp": fp},
    ).first()
    return None if row is None else int(row[0])


# ---- lots ----------------------------------------------------------------------------------


def current_book(db: Session, as_of: date | None = None) -> LotBook:
    return build_lots(load_effective(db), load_actions(db), as_of)


def rebuild_lots(db: Session) -> LotBook:
    """Recompute lots and consumptions from the effective ledger (they are a derived cache).

    Raises NegativeHoldingError / LedgerError if the ledger is impossible; the caller's
    transaction should then roll back.
    """
    book = current_book(db)
    db.execute(text("DELETE FROM lot_consumptions"))
    db.execute(text("DELETE FROM lots"))
    if book.lots:
        db.execute(
            text(
                """INSERT INTO lots (id, transaction_id, corporate_action_id, adjusted_by_ca_id, asset_id,
                       account_id, acquired_on, original_qty, remaining_qty, cost_inr, cost_orig,
                       remaining_cost_inr, fx_rate_type, is_bonus)
                   VALUES (:id, :transaction_id, :corporate_action_id, :adjusted_by_ca_id, :asset_id,
                       :account_id, :acquired_on, :original_qty, :remaining_qty, :cost_inr, :cost_orig,
                       :remaining_cost_inr, :fx_rate_type, :is_bonus)"""
            ),
            [asdict(lot) for lot in book.lots],
        )
    if book.consumptions:
        db.execute(
            text(
                """INSERT INTO lot_consumptions (id, lot_id, disposal_transaction_id, qty, cost_inr_consumed,
                       proceeds_inr, holding_days)
                   VALUES (:id, :lot_id, :disposal_transaction_id, :qty, :cost_inr_consumed, :proceeds_inr,
                       :holding_days)"""
            ),
            [asdict(c) for c in book.consumptions],
        )
    return book
