"""Effective view of the ledger (Python mirror of SQL view `transactions_effective`).

A CORRECTION row supersedes exactly one earlier row; the superseded row drops out and the
correction is read as its `corrected_type`. A correction with zero quantity and zero cash is a void.
"""

from collections.abc import Sequence

from arthashreni.ledger.types import LedgerError, TxnRow


def resolve_effective(rows: Sequence[TxnRow]) -> list[TxnRow]:
    ids = {r.id for r in rows}
    superseded: set[int] = set()
    for r in rows:
        if r.supersedes_id is None:
            continue
        if r.supersedes_id not in ids:
            raise LedgerError(f"transaction {r.id} corrects unknown transaction {r.supersedes_id}")
        if r.supersedes_id >= r.id:
            raise LedgerError(f"transaction {r.id} must correct an earlier transaction")
        if r.supersedes_id in superseded:
            raise LedgerError(f"transaction {r.supersedes_id} is already corrected; correct the correction")
        superseded.add(r.supersedes_id)
    return [r for r in rows if r.id not in superseded and not r.is_void]
