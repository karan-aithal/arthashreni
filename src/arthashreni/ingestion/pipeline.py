"""Import workflow (C4): parse -> fingerprint -> duplicate check -> preview batch -> approve -> commit.

Preview stores the raw row and its normalised candidate in append-only `transaction_imports_raw`.
Nothing reaches the ledger until `commit_batch`, which also rebuilds lots and reconciles against
statement holdings. A failure anywhere in commit rolls the whole transaction back.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.core.config import Dataset
from arthashreni.ingestion import cas, console, csv_template, detect, indmoney
from arthashreni.ingestion.common import (
    CORPORATE_ACTION,
    STATEMENT_HOLDING,
    TRANSACTION,
    ImportRowError,
    ParsedFile,
)
from arthashreni.ingestion.tables import MAX_FILE_BYTES
from arthashreni.ledger.assets import Asset, resolve_asset
from arthashreni.ledger.fingerprint import with_occurrence
from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.models import NewCorporateAction, NewStatementHolding, NewTxn
from arthashreni.ledger.reconciliation import OpenDifference, open_differences, reconcile_all
from arthashreni.ledger.repository import (
    account_ids,
    insert_corporate_action,
    insert_statement_holding,
    insert_transaction,
    load_actions,
    load_effective,
    rebuild_lots,
)
from arthashreni.ledger.types import ActionType, CorporateAction, LedgerError, TxnRow


class DbResolver:
    """Resolves asset references and account labels against the database, with caching."""

    def __init__(self, db: Session, dataset: Dataset) -> None:
        self._db, self._dataset = db, dataset
        self._assets: dict[str, Asset] = {}
        self._accounts = account_ids(db)

    def asset(self, ref: str) -> Asset:
        if ref not in self._assets:
            self._assets[ref] = resolve_asset(self._db, self._dataset, ref)
        return self._assets[ref]

    def account(self, label: str) -> int:
        if label not in self._accounts:
            raise ImportRowError(f"unknown account {label!r} (create it with `arthashreni account add`)")
        return self._accounts[label]


@dataclass
class PreviewResult:
    outcome: str  # PREVIEW | ALREADY_IMPORTED | NEEDS_INPUT
    message: str = ""
    batch_id: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CommitResult:
    inserted: dict[str, int]
    skipped: int
    reconciliation: dict[str, int]
    open_differences: list[OpenDifference]


def file_sha256(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ImportRowError(f"{path.name} is larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse(
    path: Path,
    kind: str,
    resolver: DbResolver,
    account: str | None,
    as_of: date | None,
    password: str | None,
) -> ParsedFile:
    if kind == detect.CAS_PDF:
        assert password is not None
        return cas.parse_cas(cas.read_cas(path, password), resolver, path.name)
    if kind == detect.TRANSACTIONS:
        return csv_template.parse_transactions(path, resolver)
    if kind == detect.CORPORATE_ACTIONS:
        return csv_template.parse_corporate_actions(path, resolver)
    if kind == detect.HOLDINGS:
        return csv_template.parse_holdings(path, resolver)
    assert account is not None
    if kind == detect.ZERODHA_TRADEBOOK:
        return console.parse_tradebook(path, resolver, account)
    if kind == detect.ZERODHA_HOLDINGS:
        assert as_of is not None
        return console.parse_holdings(path, resolver, account, as_of)
    return indmoney.parse_statement(path, resolver, account)


def preview_file(
    db: Session,
    dataset: Dataset,
    path: Path,
    *,
    account: str | None = None,
    as_of: date | None = None,
    password: str | None = None,
) -> PreviewResult:
    sha = file_sha256(path)
    prior = db.execute(
        text("SELECT id, status FROM import_batches WHERE file_sha256 = :s AND status <> 'REJECTED'"),
        {"s": sha},
    ).first()
    if prior:
        return PreviewResult(
            "ALREADY_IMPORTED", f"same file already imported as batch {prior[0]} ({prior[1]}); nothing to do",
            batch_id=int(prior[0]),
        )  # fmt: skip

    kind = detect.detect_format(path)
    if kind is None:
        return PreviewResult("NEEDS_INPUT", f"{path.name}: unrecognised file format")
    if kind in detect.NEEDS_ACCOUNT and not account:
        return PreviewResult(
            "NEEDS_INPUT", f"{path.name}: choose the account with --account (file does not name one)"
        )
    if kind == detect.ZERODHA_HOLDINGS and as_of is None:
        return PreviewResult("NEEDS_INPUT", f"{path.name}: holdings need --as-of DATE")
    if kind == detect.CAS_PDF and not password:
        return PreviewResult(
            "NEEDS_INPUT", f"{path.name}: CAS PDFs need their password (entered per import, never stored)"
        )

    parsed = _parse(path, kind, DbResolver(db, dataset), account, as_of, password)
    _fingerprint_and_flag_duplicates(db, parsed)
    blocking = list(parsed.blocking)
    blocking += _blocking_rows(parsed) + _simulate(db, parsed)

    counts: dict[str, int] = {}
    for r in parsed.rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = {"counts": counts, "blocking": blocking, "warnings": parsed.warnings}

    batch_id = int(
        db.execute(
            text(
                """INSERT INTO import_batches (source_kind, file_name, file_sha256, status,
                       statement_period_start, statement_period_end, summary)
                   VALUES (:k, :n, :s, 'PREVIEW', :ps, :pe, CAST(:sm AS jsonb)) RETURNING id"""
            ),
            {"k": parsed.source_kind, "n": path.name, "s": sha, "ps": parsed.period_start,
             "pe": parsed.period_end, "sm": json.dumps(summary)},
        ).scalar_one()
    )  # fmt: skip
    insert_raw = text(
        """INSERT INTO transaction_imports_raw (batch_id, row_no, item_kind, raw, candidate, fingerprint,
               parse_status, reject_reason)
           VALUES (:b, :n, :k, CAST(:raw AS jsonb), CAST(:cand AS jsonb), :fp, :st, :why)"""
    )
    db.execute(
        insert_raw,
        [
            {
                "b": batch_id, "n": r.row_no, "k": r.item_kind, "raw": json.dumps(r.raw, default=str),
                "cand": r.candidate.model_dump_json() if r.candidate else None,
                "fp": r.fingerprint, "st": r.status, "why": r.reason,
            }
            for r in parsed.rows
        ],
    )  # fmt: skip
    rows = [
        {"row_no": r.row_no, "kind": r.item_kind, "status": r.status, "reason": r.reason or ""}
        for r in parsed.rows
    ]
    return PreviewResult("PREVIEW", batch_id=batch_id, counts=counts, blocking=blocking,
                         warnings=parsed.warnings, rows=rows)  # fmt: skip


def _fingerprint_and_flag_duplicates(db: Session, parsed: ParsedFile) -> None:
    keyed = [r for r in parsed.rows if r.key is not None and r.status == "NEW"]
    fps = with_occurrence([(r.item_kind, *r.key) for r in keyed if r.key is not None])
    for r, fp in zip(keyed, fps, strict=True):
        r.fingerprint = fp
    if not fps:
        return
    known = {
        str(x[0])
        for x in db.execute(
            text(
                """SELECT fingerprint FROM transactions WHERE fingerprint = ANY(:f)
                   UNION SELECT fingerprint FROM corporate_actions WHERE fingerprint = ANY(:f)
                   UNION SELECT fingerprint FROM statement_holdings WHERE fingerprint = ANY(:f)"""
            ),
            {"f": fps},
        )
    }
    for r in keyed:
        if r.fingerprint in known:
            r.status, r.reason = "DUPLICATE", "already in the ledger"


def _blocking_rows(parsed: ParsedFile) -> list[str]:
    return [
        f"row {r.row_no}: {r.reason}"
        for r in parsed.rows
        if r.status == "REJECTED" and (r.reason or "").startswith("UNITS_MISMATCH")
    ]


def _simulate(db: Session, parsed: ParsedFile) -> list[str]:
    """Would the new rows make the ledger impossible (e.g. a sale larger than the holding)?"""
    new = [r for r in parsed.rows if r.status == "NEW"]
    new_txns = [r for r in new if isinstance(r.candidate, NewTxn)]
    new_actions = [r for r in new if isinstance(r.candidate, NewCorporateAction)]
    if not new_txns and not new_actions:
        return []
    existing = load_effective(db)
    start = max((t.id for t in existing), default=0)
    txns = list(existing)
    for n, r in enumerate(new_txns, start=1):
        c = r.candidate
        assert isinstance(c, NewTxn)
        txns.append(
            TxnRow(start + n, c.asset_id, c.account_id, c.type, c.trade_date, c.quantity, c.price, c.currency,
                   c.fx_rate, c.fx_rate_type, c.fees, c.taxes, c.stamp_duty, c.net_cash_inr, c.net_cash_orig)
        )  # fmt: skip
    actions = list(load_actions(db))
    base = max((a.id for a in actions), default=0)
    for n, r in enumerate(new_actions, start=1):
        c = r.candidate
        assert isinstance(c, NewCorporateAction)
        actions.append(CorporateAction(base + n, c.asset_id, ActionType(c.action_type), c.ex_date,
                                       c.ratio_from, c.ratio_to, c.successor_asset_id))  # fmt: skip
    try:
        build_lots(txns, actions)
    except LedgerError as exc:
        return [f"ledger check: {exc}"]
    return []


def commit_batch(db: Session, batch_id: int, *, accept_skipped: bool = False) -> CommitResult:
    batch = db.execute(
        text("SELECT status, summary FROM import_batches WHERE id = :id FOR UPDATE"), {"id": batch_id}
    ).first()
    if batch is None:
        raise LedgerError(f"batch {batch_id} does not exist")
    if batch[0] != "PREVIEW":
        raise LedgerError(f"batch {batch_id} is {batch[0]}, not PREVIEW")
    blocking = batch[1].get("blocking", [])
    if blocking:
        raise LedgerError("batch has blocking problems: " + "; ".join(blocking))

    rows = db.execute(
        text(
            """SELECT id, item_kind, candidate, fingerprint, parse_status FROM transaction_imports_raw
               WHERE batch_id = :b ORDER BY row_no"""
        ),
        {"b": batch_id},
    ).all()
    skipped = [r for r in rows if r[4] in ("REJECTED", "AMBIGUOUS")]
    if skipped and not accept_skipped:
        raise LedgerError(
            f"{len(skipped)} rows are rejected or ambiguous and would be left out; fix the source, "
            "or commit with --accept-skipped to leave them out on purpose"
        )

    inserted = {TRANSACTION: 0, CORPORATE_ACTION: 0, STATEMENT_HOLDING: 0}
    for raw_id, kind, candidate, fp, status in rows:
        if status != "NEW":
            continue
        if kind == TRANSACTION:
            new_id = insert_transaction(
                db, NewTxn.model_validate(candidate), fp=fp, import_batch_id=batch_id, raw_record_id=raw_id
            )
        elif kind == CORPORATE_ACTION:
            new_id = insert_corporate_action(
                db, NewCorporateAction.model_validate(candidate), fp=fp, import_batch_id=batch_id
            )
        else:
            new_id = insert_statement_holding(
                db, NewStatementHolding.model_validate(candidate), fp=fp, import_batch_id=batch_id
            )
        inserted[kind] += 1 if new_id is not None else 0

    rebuild_lots(db)  # raises on an impossible ledger; the caller's transaction rolls back
    recon = reconcile_all(db)
    outcome = {"inserted": inserted, "skipped": len(skipped), "reconciliation": recon}
    db.execute(
        text(
            """UPDATE import_batches SET status = 'COMMITTED', approved_at = now(),
                   summary = summary || CAST(:s AS jsonb) WHERE id = :id"""
        ),
        {"id": batch_id, "s": json.dumps({"committed": outcome})},
    )
    return CommitResult(inserted, len(skipped), recon, open_differences(db))


def reject_batch(db: Session, batch_id: int) -> None:
    done = db.execute(
        text("UPDATE import_batches SET status = 'REJECTED' WHERE id = :id AND status = 'PREVIEW'"),
        {"id": batch_id},
    )
    if done.rowcount != 1:  # type: ignore[attr-defined]
        raise LedgerError(f"batch {batch_id} is not a PREVIEW batch")


def batch_rows(db: Session, batch_id: int) -> list[dict[str, Any]]:
    return [
        {"row_no": r[0], "kind": r[1], "status": r[2], "reason": r[3] or ""}
        for r in db.execute(
            text(
                """SELECT row_no, item_kind, parse_status, reject_reason FROM transaction_imports_raw
                   WHERE batch_id = :b ORDER BY row_no"""
            ),
            {"b": batch_id},
        )
    ]
