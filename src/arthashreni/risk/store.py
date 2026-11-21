"""Database side of the risk package: signals, levels and de-duplicated alerts. Everything is appended with its
`recorded_at`; alerts are the only mutable rows (last seen, state)."""

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.risk.alerts import Alert, AlertDiff
from arthashreni.risk.levels import LevelResult
from arthashreni.risk.signals import Signal
from arthashreni.risk.thresholds import Level


def save_signals(db: Session, day: date, signals: Sequence[Signal]) -> None:
    for s in signals:
        db.execute(
            text(
                """INSERT INTO risk_signals (as_of, signal_id, scope, asset_id, value, level, status, threshold_ref, notes)
                   VALUES (:d, :i, :sc, :a, :v, :l, :st, :t, :n)"""
            ),
            {"d": day, "i": s.id, "sc": s.scope.value, "a": s.asset_id, "v": s.value.value,
             "l": None if s.level is None else s.level.name, "st": s.value.status.value,
             "t": s.kind or None, "n": list(s.value.notes)},
        )  # fmt: skip


def save_level(db: Session, day: date, r: LevelResult) -> None:
    drivers: dict[str, Any] = {
        "at_or_above": r.at_or_above, "available": r.available, "total": r.total,
        "drivers": list(r.drivers), "missing": list(r.missing),
    }  # fmt: skip
    db.execute(
        text("INSERT INTO risk_levels (as_of, level, drivers) VALUES (:d, :l, CAST(:j AS jsonb))"),
        {"d": day, "l": r.level.name, "j": json.dumps(drivers)},
    )


def latest_level(db: Session) -> tuple[date, Level, dict[str, Any]] | None:
    row = db.execute(text("SELECT as_of, level, drivers FROM risk_levels ORDER BY id DESC LIMIT 1")).first()
    return None if row is None else (row[0], Level[row[1]], row[2])


def open_alert_keys(db: Session) -> set[str]:
    return {r[0] for r in db.execute(text("SELECT dedupe_key FROM alerts WHERE state = 'OPEN'")).all()}


def _body(a: Alert) -> str:
    def num(x: Decimal | None) -> str | None:
        return None if x is None else str(x)

    return json.dumps({
        "what": a.what, "when": a.when.isoformat(), "level": a.level.name, "assets": list(a.assets),
        "evidence": list(a.evidence), "sources": list(a.sources),
        "impact_inr": num(a.impact_inr.value) if a.impact_inr else None,
        "impact_status": a.impact_inr.status.value if a.impact_inr else None,
        "classification": a.classification, "scenarios": list(a.scenarios), "responses": list(a.responses),
        "if_nothing": a.if_nothing, "watch": a.watch,
    })  # fmt: skip


def apply_alert_diff(db: Session, diff: AlertDiff) -> None:
    """New alerts are inserted (or re-opened); unchanged ones only get `last_seen_at`; cleared ones close."""
    for a in diff.new:
        db.execute(
            text(
                """INSERT INTO alerts (dedupe_key, body) VALUES (:k, CAST(:b AS jsonb))
                   ON CONFLICT (dedupe_key) DO UPDATE SET state = 'OPEN', last_seen_at = now(), body = EXCLUDED.body"""
            ),
            {"k": a.key, "b": _body(a)},
        )
    for key in diff.unchanged:
        db.execute(text("UPDATE alerts SET last_seen_at = now() WHERE dedupe_key = :k"), {"k": key})
    for key in diff.cleared:
        db.execute(text("UPDATE alerts SET state = 'CLOSED' WHERE dedupe_key = :k"), {"k": key})
