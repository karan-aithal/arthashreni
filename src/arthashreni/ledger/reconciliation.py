"""Ledger vs statement reconciliation. An unexplained difference blocks the monthly review (E3)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.ledger.lots import build_lots
from arthashreni.ledger.reconcile import unit_decimals, unit_diff
from arthashreni.ledger.repository import load_actions, load_effective
from arthashreni.ledger.types import ZERO, LedgerError, LotBook


@dataclass(frozen=True)
class OpenDifference:
    reconciliation_id: int
    account_id: int
    asset_id: int
    as_of: date
    ledger_units: Decimal
    statement_units: Decimal
    diff: Decimal


def reconcile_all(db: Session) -> dict[str, int]:
    """Compare every statement holding with the ledger as of its date. Returns status counts."""
    holdings = db.execute(
        text(
            """SELECT sh.id, sh.account_id, sh.asset_id, sh.as_of, sh.units, a.asset_class
               FROM statement_holdings sh JOIN ref.assets a ON a.id = sh.asset_id ORDER BY sh.id"""
        )
    ).all()
    existing = {
        int(r[0]): (r[1], r[2])
        for r in db.execute(text("SELECT statement_holding_id, status, accepted_diff FROM reconciliations"))
    }
    txns, actions = load_effective(db), load_actions(db)
    books: dict[date, LotBook] = {}
    counts = {"MATCHED": 0, "UNEXPLAINED": 0, "ACCEPTED": 0}
    for sh_id, account_id, asset_id, as_of, units, asset_class in holdings:
        book = books.setdefault(as_of, build_lots(txns, actions, as_of))
        ledger_units = book.holdings().get((int(account_id), int(asset_id)), ZERO)
        statement_units = Decimal(str(units))
        diff = unit_diff(ledger_units, statement_units, unit_decimals(asset_class))
        status = "MATCHED" if diff == 0 else "UNEXPLAINED"
        reason: str | None = None
        accepted_diff: Decimal | None = None
        prior = existing.get(int(sh_id))
        if status == "UNEXPLAINED" and prior and prior[0] == "ACCEPTED":
            accepted_diff = Decimal(str(prior[1]))
            if accepted_diff == diff:  # same difference the investor already accepted
                status = "ACCEPTED"
        params = {
            "sh": int(sh_id),
            "l": ledger_units,
            "s": statement_units,
            "d": diff,
            "st": status,
        }
        if prior is None:
            db.execute(
                text(
                    """INSERT INTO reconciliations (statement_holding_id, ledger_units, statement_units, diff,
                           status) VALUES (:sh, :l, :s, :d, :st)"""
                ),
                params,
            )
        elif status == "ACCEPTED":
            db.execute(
                text(
                    """UPDATE reconciliations SET run_at = now(), ledger_units = :l, statement_units = :s,
                           diff = :d WHERE statement_holding_id = :sh"""
                ),
                params,
            )
        else:
            db.execute(
                text(
                    """UPDATE reconciliations SET run_at = now(), ledger_units = :l, statement_units = :s,
                           diff = :d, status = :st, accepted_reason = :r, accepted_diff = NULL
                       WHERE statement_holding_id = :sh"""
                ),
                {**params, "r": reason},
            )
        counts[status] += 1
    return counts


def open_differences(db: Session) -> list[OpenDifference]:
    """Unexplained differences on the latest statement for each (account, asset)."""
    rows = db.execute(
        text(
            """SELECT r.id, sh.account_id, sh.asset_id, sh.as_of, r.ledger_units, r.statement_units, r.diff
               FROM reconciliations r JOIN statement_holdings sh ON sh.id = r.statement_holding_id
               WHERE r.status = 'UNEXPLAINED'
                 AND sh.as_of = (SELECT max(x.as_of) FROM statement_holdings x
                                 WHERE x.account_id = sh.account_id AND x.asset_id = sh.asset_id)
               ORDER BY r.id"""
        )
    ).all()
    return [
        OpenDifference(
            int(r[0]), int(r[1]), int(r[2]), r[3], Decimal(str(r[4])), Decimal(str(r[5])), Decimal(str(r[6]))
        )
        for r in rows
    ]


def accept_difference(db: Session, reconciliation_id: int, reason: str) -> None:
    if not reason.strip():
        raise LedgerError("accepting a difference needs a reason")
    done = db.execute(
        text(
            """UPDATE reconciliations SET status = 'ACCEPTED', accepted_reason = :r, accepted_diff = diff
               WHERE id = :id AND status = 'UNEXPLAINED'"""
        ),
        {"id": reconciliation_id, "r": reason.strip()},
    )
    if done.rowcount != 1:  # type: ignore[attr-defined]
        raise LedgerError(f"reconciliation {reconciliation_id} is not an open unexplained difference")


@dataclass(frozen=True)
class ReportLine:
    account: str
    asset: str
    as_of: date
    ledger_units: Decimal
    statement_units: Decimal
    diff: Decimal
    status: str
    reason: str | None


def reconciliation_report(db: Session) -> list[ReportLine]:
    """Every statement holding with its reconciliation result, newest statement first."""
    rows = db.execute(
        text(
            """SELECT ac.label, a.name, sh.as_of, r.ledger_units, r.statement_units, r.diff, r.status,
                      r.accepted_reason
               FROM reconciliations r
               JOIN statement_holdings sh ON sh.id = r.statement_holding_id
               JOIN accounts ac ON ac.id = sh.account_id
               JOIN ref.assets a ON a.id = sh.asset_id
               ORDER BY sh.as_of DESC, ac.label, a.name"""
        )
    ).all()
    return [
        ReportLine(r[0], r[1], r[2], Decimal(str(r[3])), Decimal(str(r[4])), Decimal(str(r[5])), r[6], r[7])
        for r in rows
    ]
