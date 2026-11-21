"""Database side of the policy package: budget settings, SIP mandates, reserves, IPS versions, personal context,
IPO applications, the one-time allocation of a month and the investor's decisions.

Allocation is one transaction that first takes `SELECT ... FOR UPDATE` on the month's `budget_periods` row, so two
allocations for one month run one after the other and the second sees `ALLOCATED` (ADR-0009). The constraint
triggers and `UNIQUE(envelope_line_id)` are the backstop if this code were ever bypassed. Recording a decision
executes nothing (invariant 2); the only side effect is the investor's own reserve bookkeeping."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.policy.context import Certainty, Context, ContextField
from arthashreni.policy.envelope import Kind, Mandate
from arthashreni.policy.ipo import ipo_verdict
from arthashreni.policy.ips import IPS
from arthashreni.policy.recommendation import Recommendation

IN_KINDS = ("TRANSFER_IN", "DIVIDEND_IN", "SALE_PROCEEDS_IN", "IPO_RELEASE")


class PolicyError(RuntimeError):
    pass


class PeriodAllocated(PolicyError):
    """The month already has an allocation; there is one recommendation per month."""


def _d(v: Any) -> Decimal:
    return Decimal(str(v))


# ---- budget ----------------------------------------------------------------------------------------------


def set_budget(db: Session, amount: Decimal, effective_from: date) -> int:
    return int(
        db.execute(
            text(
                "INSERT INTO budget_settings (monthly_budget_inr, effective_from) VALUES (:a, :d) RETURNING id"
            ),
            {"a": amount, "d": effective_from},
        ).scalar_one()
    )


def budget_for(db: Session, period: date) -> tuple[int, Decimal] | None:
    """The newest setting effective on the first day of `period`; history is never overwritten."""
    row = db.execute(
        text(
            "SELECT id, monthly_budget_inr FROM budget_settings WHERE effective_from <= :p "
            "ORDER BY effective_from DESC, id DESC LIMIT 1"
        ),
        {"p": period},
    ).first()
    return None if row is None else (int(row[0]), _d(row[1]))


# ---- SIP mandates ---------------------------------------------------------------------------------------


def add_sip(
    db: Session,
    asset_id: int,
    account_id: int,
    amount: Decimal,
    day: int,
    platform: str,
    start: date,
    end: date | None = None,
    *,
    supersedes_id: int | None = None,
    status: str = "ACTIVE",
) -> int:
    return int(
        db.execute(
            text(
                """INSERT INTO sip_mandates (supersedes_id, asset_id, account_id, amount_inr, day_of_month,
                       platform, start_date, end_date, status)
                   VALUES (:s, :a, :c, :m, :d, :p, :st, :e, :t) RETURNING id"""
            ),
            {"s": supersedes_id, "a": asset_id, "c": account_id, "m": amount, "d": day, "p": platform,
             "st": start, "e": end, "t": status},
        ).scalar_one()
    )  # fmt: skip


def load_mandates(db: Session) -> list[Mandate]:
    """The latest version of every mandate (a change is a new row that supersedes the old one)."""
    rows = db.execute(
        text(
            """SELECT id, asset_id, account_id, amount_inr, day_of_month, platform, start_date, end_date, status
               FROM sip_mandates m WHERE NOT EXISTS (SELECT 1 FROM sip_mandates n WHERE n.supersedes_id = m.id)
               ORDER BY id"""
        )
    ).all()
    return [Mandate(r[0], r[1], r[2], _d(r[3]), r[4], r[5], r[6], r[7], r[8]) for r in rows]


def change_sip(
    db: Session, mandate_id: int, *, amount: Decimal | None = None, status: str | None = None,
    end_date: date | None = None,
) -> int:  # fmt: skip
    cur = next((m for m in load_mandates(db) if m.id == mandate_id), None)
    if cur is None:
        raise PolicyError(f"mandate {mandate_id} is not the latest version (or does not exist)")
    return add_sip(
        db, cur.asset_id, cur.account_id, amount or cur.amount_inr, cur.day_of_month, cur.platform,
        cur.start_date, end_date or cur.end_date, supersedes_id=cur.id, status=status or cur.status,
    )  # fmt: skip


# ---- reserves -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReserveBalance:
    id: int
    name: str
    kind: str
    target_inr: Decimal | None
    available_inr: Decimal
    blocked_inr: Decimal


def ensure_reserve(db: Session, name: str, kind: str, target: Decimal | None = None) -> int:
    row = db.execute(text("SELECT id FROM reserves WHERE name = :n"), {"n": name}).first()
    if row:
        return int(row[0])
    return int(
        db.execute(
            text("INSERT INTO reserves (name, kind, target_inr) VALUES (:n, :k, :t) RETURNING id"),
            {"n": name, "k": kind, "t": target},
        ).scalar_one()
    )


def reserve_balances(db: Session) -> list[ReserveBalance]:
    rows = db.execute(
        text(
            """SELECT r.id, r.name, r.kind, r.target_inr,
                      coalesce(sum(CASE WHEN l.kind IN ('TRANSFER_IN','DIVIDEND_IN','SALE_PROCEEDS_IN','IPO_RELEASE')
                                        THEN l.amount_inr WHEN l.kind IN ('USE','IPO_BLOCK') THEN -l.amount_inr
                                        ELSE 0 END), 0),
                      coalesce(sum(CASE WHEN l.kind = 'IPO_BLOCK' THEN l.amount_inr
                                        WHEN l.kind IN ('IPO_RELEASE','IPO_ALLOT') THEN -l.amount_inr
                                        ELSE 0 END), 0)
               FROM reserves r LEFT JOIN reserve_ledger l ON l.reserve_id = r.id
               GROUP BY r.id ORDER BY r.id"""
        )
    ).all()
    return [
        ReserveBalance(r[0], r[1], r[2], None if r[3] is None else _d(r[3]), _d(r[4]), _d(r[5])) for r in rows
    ]


def reserve_add(
    db: Session, reserve_id: int, kind: str, amount: Decimal, on: date, *, approved_line_id: int | None = None,
    sweep_period: date | None = None, note: str | None = None,
) -> int:  # fmt: skip
    """Append a reserve entry. The database rejects one that would make a balance negative."""
    return int(
        db.execute(
            text(
                """INSERT INTO reserve_ledger (reserve_id, entry_date, kind, amount_inr, approved_line_id,
                       sweep_period, note) VALUES (:r, :d, :k, :a, :l, :s, :n) RETURNING id"""
            ),
            {
                "r": reserve_id,
                "d": on,
                "k": kind,
                "a": amount,
                "l": approved_line_id,
                "s": sweep_period,
                "n": note,
            },
        ).scalar_one()
    )


# ---- IPS and personal context ----------------------------------------------------------------------------


def create_ips_draft(db: Session, ips: IPS) -> int:
    version = int(db.execute(text("SELECT coalesce(max(version), 0) + 1 FROM ips_versions")).scalar_one())
    db.execute(
        text("INSERT INTO ips_versions (version, status, body) VALUES (:v, 'DRAFT', CAST(:b AS jsonb))"),
        {"v": version, "b": ips.model_dump_json()},
    )
    return version


def approve_ips(db: Session, version: int, note: str) -> None:
    """Only the investor calls this (the CLI asks for a note). The previous approved version is superseded."""
    if not note.strip():
        raise PolicyError("approving an IPS needs a note")
    row = db.execute(text("SELECT status FROM ips_versions WHERE version = :v"), {"v": version}).first()
    if row is None or row[0] != "DRAFT":
        raise PolicyError(f"IPS version {version} is not a draft")
    db.execute(text("UPDATE ips_versions SET status = 'SUPERSEDED' WHERE status = 'APPROVED'"))
    db.execute(
        text(
            "UPDATE ips_versions SET status = 'APPROVED', approved_at = now(), approved_note = :n WHERE version = :v"
        ),
        {"n": note.strip(), "v": version},
    )


def approved_ips(db: Session) -> tuple[int, int, IPS] | None:
    """(row id, version, IPS) of the approved version; drafts are never returned."""
    row = db.execute(text("SELECT id, version, body FROM ips_versions WHERE status = 'APPROVED'")).first()
    return None if row is None else (int(row[0]), int(row[1]), IPS.model_validate(row[2]))


def set_context(db: Session, name: str, value: Any, certainty: Certainty) -> None:
    db.execute(
        text("INSERT INTO personal_context (field, value, certainty) VALUES (:f, CAST(:v AS jsonb), :c)"),
        {"f": name, "v": json.dumps(value), "c": certainty.value},
    )


def load_context(db: Session) -> Context:
    rows = db.execute(
        text(
            "SELECT DISTINCT ON (field) field, value, certainty FROM personal_context ORDER BY field, id DESC"
        )
    ).all()
    return {r[0]: ContextField(value=r[1], certainty=Certainty(r[2])) for r in rows}


# ---- IPO sinking fund ------------------------------------------------------------------------------------


def add_ipo(
    db: Session, ref: str, board: str, rhp_document: str | None, min_lot_value: Decimal | None, research_class: str,
) -> None:  # fmt: skip
    """SME IPOs are stored as EXCLUDED: tracked, never eligible (B3)."""
    status = "PLANNED" if board == "MAINBOARD" else "EXCLUDED"
    db.execute(
        text(
            """INSERT INTO ipo_applications (ipo_ref, board, rhp_document, min_lot_value_inr, research_class, status)
               VALUES (:r, :b, :d, :m, :c, :s)"""
        ),
        {"r": ref, "b": board, "d": rhp_document, "m": min_lot_value, "c": research_class, "s": status},
    )


def _ipo(db: Session, ref: str) -> Any:
    row = db.execute(
        text(
            "SELECT board, min_lot_value_inr, research_class, status FROM ipo_applications WHERE ipo_ref = :r"
        ),
        {"r": ref},
    ).first()
    if row is None:
        raise PolicyError(f"no IPO {ref!r}")
    return row


def ipo_block(db: Session, ref: str, reserve_id: int, amount: Decimal, on: date) -> None:
    """ASBA block: money leaves the available balance but stays committed until released (B3)."""
    board, min_lot, research, status = _ipo(db, ref)
    available = next(b.available_inr for b in reserve_balances(db) if b.id == reserve_id)
    verdict = ipo_verdict(board, None if min_lot is None else _d(min_lot), research, available)
    if status != "PLANNED" or not verdict.can_apply or (min_lot is not None and amount < _d(min_lot)):
        raise PolicyError(
            f"cannot apply to {ref}: " + "; ".join(verdict.reasons or ("status or amount not allowed",))
        )
    reserve_add(db, reserve_id, "IPO_BLOCK", amount, on, note=ref)
    db.execute(text("UPDATE ipo_applications SET status = 'APPLIED', applied_on = :d WHERE ipo_ref = :r"),
               {"d": on, "r": ref})  # fmt: skip


def ipo_settle(db: Session, ref: str, reserve_id: int, allotted: Decimal, on: date) -> str:
    """Allotment, partial allotment or refund as separate reserve events: what was allotted is consumed and the
    rest of the blocked money is released. Returns the new status."""
    _, _, _, status = _ipo(db, ref)
    if status != "APPLIED":
        raise PolicyError(f"{ref} is {status}, not APPLIED")
    blocked = _d(db.execute(text("SELECT sum(amount_inr) FROM reserve_ledger WHERE kind = 'IPO_BLOCK' AND note = :r"), {"r": ref}).scalar_one())  # fmt: skip
    if not 0 <= allotted <= blocked:
        raise PolicyError("allotted amount must be between 0 and the blocked amount")
    if allotted > 0:
        reserve_add(db, reserve_id, "IPO_ALLOT", allotted, on, note=ref)
    if blocked - allotted > 0:
        reserve_add(db, reserve_id, "IPO_RELEASE", blocked - allotted, on, note=ref)
    new = "ALLOTTED" if allotted == blocked else "NOT_ALLOTTED" if allotted == 0 else "PARTIAL"
    db.execute(text("UPDATE ipo_applications SET status = :s WHERE ipo_ref = :r"), {"s": new, "r": ref})
    return new


# ---- allocation of a month, decisions --------------------------------------------------------------------


def allocate_period(db: Session, rec: Recommendation, ips_row_id: int | None) -> int:
    """Store the month's envelope and recommendation once. Returns the recommendation id."""
    env = rec.envelope
    setting = budget_for(db, env.period)
    if setting is None or setting[1] != env.opening_budget_inr:
        raise PolicyError("the recommendation's budget is not the budget setting effective for that month")
    db.execute(
        text(
            """INSERT INTO budget_periods (period, budget_setting_id, opening_budget_inr, committed_sip_inr,
                   unallocated_inr) VALUES (:p, :b, :o, 0, :o) ON CONFLICT (period) DO NOTHING"""
        ),
        {"p": env.period, "b": setting[0], "o": env.opening_budget_inr},
    )
    status = db.execute(
        text("SELECT status FROM budget_periods WHERE period = :p FOR UPDATE"), {"p": env.period}
    ).scalar_one()
    if status == "ALLOCATED":
        raise PeriodAllocated(f"{env.period:%Y-%m} already has an allocation")
    ids: list[int] = []
    for ln in env.lines:
        ids.append(int(db.execute(
            text(
                """INSERT INTO envelope_lines (period, kind, amount_inr, asset_id, reserve_id, sip_mandate_id)
                   VALUES (:p, :k, :a, :as, :r, :m) RETURNING id"""
            ),
            {"p": env.period, "k": ln.kind.value, "a": ln.amount_inr, "as": ln.asset_id, "r": ln.reserve_id,
             "m": ln.sip_mandate_id},
        ).scalar_one()))  # fmt: skip
    rec_id = int(db.execute(
        text(
            """INSERT INTO recommendations (period, schema_version, payload, confidence, risk_level, ips_version_id,
                   human_approval_required) VALUES (:p, :v, CAST(:j AS jsonb), :c, :l, :i, true) RETURNING id"""
        ),
        {"p": env.period, "v": rec.schema_version, "j": rec.model_dump_json(), "c": rec.confidence,
         "l": rec.risk_level, "i": ips_row_id},
    ).scalar_one())  # fmt: skip
    for rl in rec.lines:
        db.execute(
            text(
                """INSERT INTO recommendation_lines (recommendation_id, envelope_line_id, action, amount_inr, asset_id,
                       detail) VALUES (:r, :e, :a, :m, :as, CAST(:d AS jsonb))"""
            ),
            {"r": rec_id, "e": None if rl.envelope_ref is None else ids[rl.envelope_ref], "a": rl.action.value,
             "m": rl.amount_inr, "as": rl.asset_id, "d": rl.model_dump_json(include={"subject", "reasons"})},
        )  # fmt: skip
    db.execute(
        text(
            """UPDATE budget_periods SET committed_sip_inr = :s, new_allocations_inr = :n, reserve_transfers_inr = :t,
                   unallocated_inr = :u, status = 'ALLOCATED', allocated_at = now() WHERE period = :p"""
        ),
        {"s": env.committed_sip_inr, "n": env.new_allocations_inr, "t": env.reserve_transfers_inr,
         "u": env.unallocated_inr, "p": env.period},
    )  # fmt: skip
    return rec_id


def load_recommendation(db: Session, period: date) -> tuple[int, Recommendation] | None:
    row = db.execute(text("SELECT id, payload FROM recommendations WHERE period = :p"), {"p": period}).first()
    return None if row is None else (int(row[0]), Recommendation.model_validate(row[1]))


def record_decision(
    db: Session, recommendation_id: int, line_id: int | None, decision: str, reason: str, on: date
) -> int:
    """The investor's decision, appended. An approved reserve transfer or reserve use is also booked in the reserve
    ledger (the investor's own bookkeeping); no trade, SIP or mandate is placed, changed or cancelled (invariant 2)."""
    if not reason.strip():
        raise PolicyError("a decision needs a reason")
    if (
        line_id is not None
        and db.execute(
            text("SELECT 1 FROM decisions WHERE line_id = :l AND decision = 'APPROVE'"), {"l": line_id}
        ).first()
    ):
        raise PolicyError(f"line {line_id} is already approved")
    did = int(db.execute(
        text(
            "INSERT INTO decisions (recommendation_id, line_id, decision, reason) VALUES (:r, :l, :d, :x) RETURNING id"
        ),
        {"r": recommendation_id, "l": line_id, "d": decision, "x": reason.strip()},
    ).scalar_one())  # fmt: skip
    if decision == "APPROVE" and line_id is not None:
        row = db.execute(
            text(
                """SELECT e.kind, e.amount_inr, e.reserve_id FROM recommendation_lines l
                   JOIN envelope_lines e ON e.id = l.envelope_line_id WHERE l.id = :l"""
            ),
            {"l": line_id},
        ).first()
        if row and row[0] == Kind.RESERVE_TRANSFER.value:
            reserve_add(db, row[2], "TRANSFER_IN", _d(row[1]), on, approved_line_id=line_id)
        elif row and row[0] == Kind.RESERVE_USE.value:
            reserve_add(db, row[2], "USE", _d(row[1]), on, approved_line_id=line_id)
    return did


def sweep_leftover(db: Session, period: date, cash_reserve_id: int, on: date) -> Decimal:
    """Close a month: whatever was not allocated moves to the cash reserve (B2); the next month still opens at the
    configured budget, so leftovers never inflate it. Idempotent per month."""
    row = db.execute(
        text("SELECT status, unallocated_inr FROM budget_periods WHERE period = :p"), {"p": period}
    ).first()
    if row is None or row[0] != "ALLOCATED":
        raise PolicyError("only an allocated month can be closed")
    left = _d(row[1])
    if (
        left > 0
        and not db.execute(
            text("SELECT 1 FROM reserve_ledger WHERE reserve_id = :r AND sweep_period = :p"),
            {"r": cash_reserve_id, "p": period},
        ).first()
    ):
        reserve_add(db, cash_reserve_id, "TRANSFER_IN", left, on, sweep_period=period, note="leftover sweep")
    return left
