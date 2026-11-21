"""Ledger and import views (read-only, Phase 6). Effective view only: corrections applied, voids removed.

A ledger row is a recorded fact, so its amounts carry status `ok` with the trade date as-of and the row id as
source. Nothing here writes; committing or rejecting an import stays in the CLI (it needs approval)."""

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from arthashreni.api.auth import RequireSession
from arthashreni.api.serialize import jsonable
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.dq.measured import Measured, QualityStatus, day_stamp

router = APIRouter(tags=["ledger"])


def _fact(value: Decimal | None, on: date, txn_id: int) -> Measured[Decimal]:
    if value is None:
        return Measured(None, QualityStatus.MISSING, day_stamp(on), (), ("not recorded",))
    return Measured(Decimal(str(value)), QualityStatus.OK, day_stamp(on), (f"txn:{txn_id}",))


@router.get("/transactions")
def transactions(
    _: RequireSession, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)
) -> dict[str, Any]:
    with session_scope() as db:
        total = db.scalar(text("SELECT count(*) FROM transactions_effective"))
        rows = db.execute(
            text(
                """SELECT t.id, t.trade_date, t.effective_type, a.name, a.ticker, ac.label, t.currency,
                          t.quantity, t.price, t.fees, t.taxes, t.stamp_duty, t.net_cash_inr, t.net_cash_orig,
                          t.fx_rate, t.fx_rate_type, t.supersedes_id
                   FROM transactions_effective t
                   LEFT JOIN ref.assets a ON a.id = t.asset_id
                   JOIN accounts ac ON ac.id = t.account_id
                   ORDER BY t.trade_date DESC, t.id DESC LIMIT :l OFFSET :o"""
            ),
            {"l": limit, "o": offset},
        ).all()
    items = [
        {
            "id": r[0],
            "trade_date": r[1],
            "type": r[2],
            "asset": r[3],
            "ticker": r[4],
            "account": r[5],
            "currency": r[6],
            "quantity": _fact(r[7], r[1], r[0]),
            "price": _fact(r[8], r[1], r[0]),
            "fees": _fact(r[9], r[1], r[0]),
            "taxes": _fact(r[10], r[1], r[0]),
            "stamp_duty": _fact(r[11], r[1], r[0]),
            "net_cash_inr": _fact(r[12], r[1], r[0]),
            "net_cash_orig": _fact(r[13], r[1], r[0]),
            "fx_rate": _fact(r[14], r[1], r[0]),
            "fx_rate_type": r[15],
            "corrects_id": r[16],
        }
        for r in rows
    ]
    return {
        "dataset": get_settings().dataset.upper(),
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": jsonable(items),
    }


@router.get("/imports")
def imports(_: RequireSession) -> dict[str, Any]:
    with session_scope() as db:
        rows = db.execute(
            text(
                """SELECT b.id, b.source_kind, b.file_name, b.status, b.received_at, b.approved_at,
                          b.statement_period_start, b.statement_period_end,
                          count(r.id) FILTER (WHERE r.parse_status = 'NEW'),
                          count(r.id) FILTER (WHERE r.parse_status = 'DUPLICATE'),
                          count(r.id) FILTER (WHERE r.parse_status = 'AMBIGUOUS'),
                          count(r.id) FILTER (WHERE r.parse_status = 'REJECTED')
                   FROM import_batches b LEFT JOIN transaction_imports_raw r ON r.batch_id = b.id
                   GROUP BY b.id ORDER BY b.id DESC"""
            )
        ).all()
    items = [
        {
            "id": r[0],
            "source_kind": r[1],
            "file_name": r[2],
            "status": r[3],
            "received_at": r[4],
            "approved_at": r[5],
            "period_start": r[6],
            "period_end": r[7],
            "rows": {"new": r[8], "duplicate": r[9], "ambiguous": r[10], "rejected": r[11]},
        }
        for r in rows
    ]
    return {"dataset": get_settings().dataset.upper(), "items": jsonable(items)}
