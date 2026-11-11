"""Writes and queue operations for market data and quarantine (E3, E4). Rows are never updated in place."""

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from arthashreni.dq.measured import QualityStatus

log = logging.getLogger(__name__)

# table -> (value column, valid column) for copying a quarantined row into a corrected / accepted new row
_VALUE_COL = {"prices": "close", "fx_rates": "rate", "fundamentals": "value"}
_COPY_COLS = {
    "prices": "asset_id, valid_at, close, currency, price_type, split_factor, source_id, raw_payload_id, import_batch_id",
    "fx_rates": "pair, rate_type, underlying_series, provider, valid_at, observed_at, rate, source_id, raw_payload_id",
    "fundamentals": "asset_id, concept, valid_at, fiscal_year, fiscal_period, form, unit, value, filed_at, "
    "accession_no, source_id, raw_payload_id",
}


def insert_raw_payload(
    db: Session,
    *,
    source_id: str,
    sha256: str,
    window_start: date,
    window_end: date,
    schema_ok: bool,
    file_path: str | None,
    http_status: int | None,
    detail: str | None = None,
) -> int:
    return int(
        db.execute(
            text(
                """INSERT INTO raw_payloads (source_id, window_start, window_end, sha256, file_path,
                       http_status, schema_ok, detail)
                   VALUES (:source_id, :ws, :we, :sha, :fp, :hs, :ok, :detail) RETURNING id"""
            ),
            {"source_id": source_id, "ws": window_start, "we": window_end, "sha": sha256, "fp": file_path,
             "hs": http_status, "ok": schema_ok, "detail": detail},
        ).scalar_one()
    )  # fmt: skip


def insert_price(
    db: Session,
    *,
    asset_id: int,
    valid_at: date,
    close: Decimal,
    currency: str,
    price_type: str,
    source_id: str,
    status: QualityStatus = QualityStatus.OK,
    notes: list[str] | None = None,
    raw_payload_id: int | None = None,
    import_batch_id: int | None = None,
    split_factor: Decimal | None = None,
    recorded_at: datetime | None = None,
) -> int:
    return int(
        db.execute(
            text(
                """INSERT INTO prices (asset_id, valid_at, close, currency, price_type, split_factor, source_id,
                       raw_payload_id, import_batch_id, quality_status, quality_notes, recorded_at)
                   VALUES (:a, :v, :c, :cur, :pt, :sf, :src, :raw, :batch, :st, :notes, COALESCE(:rec, now()))
                   RETURNING id"""
            ),
            {"a": asset_id, "v": valid_at, "c": close, "cur": currency, "pt": price_type, "sf": split_factor,
             "src": source_id, "raw": raw_payload_id, "batch": import_batch_id, "st": status.value,
             "notes": notes or [], "rec": recorded_at},
        ).scalar_one()
    )  # fmt: skip


def insert_fx(
    db: Session,
    *,
    rate_type: str,
    underlying_series: str,
    provider: str,
    valid_at: date,
    rate: Decimal,
    source_id: str,
    observed_at: datetime | None = None,
    pair: str = "USDINR",
    status: QualityStatus = QualityStatus.OK,
    notes: list[str] | None = None,
    raw_payload_id: int | None = None,
    recorded_at: datetime | None = None,
) -> int:
    return int(
        db.execute(
            text(
                """INSERT INTO fx_rates (pair, rate_type, underlying_series, provider, valid_at, observed_at, rate,
                       source_id, raw_payload_id, quality_status, quality_notes, recorded_at)
                   VALUES (:pair, :rt, :us, :prov, :v, :obs, :rate, :src, :raw, :st, :notes, COALESCE(:rec, now()))
                   RETURNING id"""
            ),
            {"pair": pair, "rt": rate_type, "us": underlying_series, "prov": provider, "v": valid_at,
             "obs": observed_at, "rate": rate, "src": source_id, "raw": raw_payload_id, "st": status.value,
             "notes": notes or [], "rec": recorded_at},
        ).scalar_one()
    )  # fmt: skip


def insert_fundamental(
    db: Session,
    *,
    asset_id: int,
    concept: str,
    valid_at: date,
    unit: str,
    value: Decimal,
    accession_no: str,
    source_id: str,
    fiscal_year: int | None = None,
    fiscal_period: str | None = None,
    form: str | None = None,
    filed_at: date | None = None,
    raw_payload_id: int | None = None,
    status: QualityStatus = QualityStatus.OK,
    notes: list[str] | None = None,
    recorded_at: datetime | None = None,
) -> int | None:
    """Returns None if this exact fact (same filing) is already stored."""
    return db.execute(
        text(
            """INSERT INTO fundamentals (asset_id, concept, valid_at, fiscal_year, fiscal_period, form, unit, value,
                   filed_at, accession_no, source_id, raw_payload_id, quality_status, quality_notes, recorded_at)
               VALUES (:a, :c, :v, :fy, :fp, :form, :u, :val, :filed, :accn, :src, :raw, :st, :notes,
                   COALESCE(:rec, now()))
               ON CONFLICT (asset_id, concept, valid_at, fiscal_period, unit, accession_no) DO NOTHING
               RETURNING id"""
        ),
        {"a": asset_id, "c": concept, "v": valid_at, "fy": fiscal_year, "fp": fiscal_period, "form": form,
         "u": unit, "val": value, "filed": filed_at, "accn": accession_no, "src": source_id, "raw": raw_payload_id,
         "st": status.value, "notes": notes or [], "rec": recorded_at},
    ).scalar_one_or_none()  # fmt: skip


def quarantine(
    db: Session,
    table_name: str,
    row_ref: str,
    check_name: str,
    detail: dict[str, Any],
    source_id: str | None = None,
) -> int | None:
    """Queue an item for review. An identical item still pending is not queued twice (de-duplicated)."""
    qid = db.execute(
        text(
            """INSERT INTO dq_quarantine (table_name, row_ref, check_name, source_id, detail)
               VALUES (:t, :r, :c, :s, CAST(:d AS jsonb))
               ON CONFLICT (table_name, row_ref, check_name) WHERE resolution = 'PENDING' DO NOTHING
               RETURNING id"""
        ),
        {
            "t": table_name,
            "r": row_ref,
            "c": check_name,
            "s": source_id,
            "d": json.dumps(detail, default=str),
        },
    ).scalar_one_or_none()
    if qid is not None:
        log.warning(
            "quarantined",
            extra={"data": {"id": qid, "table": table_name, "check": check_name, "source": source_id}},
        )
    return None if qid is None else int(qid)


def clear_pending(db: Session, table_name: str, row_ref: str, check_name: str, note: str) -> int:
    """Auto-clear a pending item whose cause has gone (e.g. an outage after a successful run)."""
    result = db.execute(
        text(
            """UPDATE dq_quarantine SET resolution = 'AUTO_CLEARED', resolved_at = now(), resolution_note = :n
               WHERE table_name = :t AND row_ref = :r AND check_name = :c AND resolution = 'PENDING'"""
        ),
        {"t": table_name, "r": row_ref, "c": check_name, "n": note},
    )
    return int(cast("CursorResult[Any]", result).rowcount)


def record_conflict(
    db: Session,
    subject: str,
    valid_at: date,
    observation_ids: list[int],
    chosen_id: int,
    rule: str,
    diff_pct: Decimal,
) -> None:
    db.execute(
        text(
            """INSERT INTO source_conflicts (subject, valid_at, observation_ids, chosen_id, rule, diff_pct)
               VALUES (:s, :v, :o, :c, :r, :d) ON CONFLICT (subject, valid_at, chosen_id) DO NOTHING"""
        ),
        {"s": subject, "v": valid_at, "o": observation_ids, "c": chosen_id, "r": rule, "d": diff_pct},
    )


def price_history(db: Session, asset_id: int, before: date, n: int) -> list[Decimal]:
    """Up to `n` newest usable closes strictly before `before`, oldest first (one per date, latest recorded)."""
    rows = db.execute(
        text(
            """SELECT close FROM (
                   SELECT DISTINCT ON (valid_at) valid_at, close FROM prices
                   WHERE asset_id = :a AND valid_at < :b AND quality_status <> 'quarantined'
                   ORDER BY valid_at DESC, recorded_at DESC, id DESC LIMIT :n) t ORDER BY valid_at"""
        ),
        {"a": asset_id, "b": before, "n": n},
    ).all()
    return [r[0] for r in rows]


def fx_history(db: Session, rate_type: str, before: date, n: int) -> list[Decimal]:
    rows = db.execute(
        text(
            """SELECT rate FROM (
                   SELECT DISTINCT ON (valid_at) valid_at, rate FROM fx_rates
                   WHERE rate_type = :t AND valid_at < :b AND quality_status <> 'quarantined'
                   ORDER BY valid_at DESC, recorded_at DESC, id DESC LIMIT :n) t ORDER BY valid_at"""
        ),
        {"t": rate_type, "b": before, "n": n},
    ).all()
    return [r[0] for r in rows]


def list_quarantine(db: Session, *, pending_only: bool = True) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """SELECT id, table_name, row_ref, check_name, source_id, detail, created_at, resolution,
                      resolved_at, resolution_note FROM dq_quarantine
               WHERE (:p = false OR resolution = 'PENDING') ORDER BY id"""
        ),
        {"p": pending_only},
    ).mappings()
    return [dict(r) for r in rows]


def resolve_quarantine(
    db: Session, qid: int, resolution: str, note: str, corrected_value: Decimal | None = None
) -> None:
    """ACCEPTED / CORRECTED add a new `ok` row that supersedes the quarantined one; REJECTED only closes it."""
    if resolution not in ("ACCEPTED", "CORRECTED", "REJECTED"):
        raise ValueError("resolution must be ACCEPTED, CORRECTED or REJECTED")
    if not note.strip():
        raise ValueError("a note is required: every action is logged with its reason")
    row = db.execute(
        text("SELECT table_name, row_ref, resolution FROM dq_quarantine WHERE id = :i FOR UPDATE"), {"i": qid}
    ).one_or_none()
    if row is None:
        raise ValueError(f"no quarantine item {qid}")
    table, ref, current = row
    if current != "PENDING":
        raise ValueError(f"item {qid} is already {current}")
    if resolution in ("ACCEPTED", "CORRECTED"):
        if table not in _VALUE_COL:
            if resolution == "CORRECTED":
                raise ValueError(f"{table} items have nothing to correct; accept or reject them")
        else:
            row_id = int(ref)
            if resolution == "CORRECTED" and (corrected_value is None or corrected_value <= 0):
                raise ValueError("CORRECTED needs a positive corrected value")
            col, cols = _VALUE_COL[table], _COPY_COLS[table]
            select_cols = ", ".join(
                ":val" if c == col and resolution == "CORRECTED" else c for c in cols.split(", ")
            )
            db.execute(
                text(
                    f"INSERT INTO {table} ({cols}, quality_status, quality_notes) "  # noqa: S608
                    f"SELECT {select_cols}, 'ok', ARRAY[:why] FROM {table} WHERE id = :id"  # fixed identifiers
                ),
                {"id": row_id, "why": f"{resolution.lower()} from quarantine #{qid}: {note}",
                 **({"val": corrected_value} if resolution == "CORRECTED" else {})},
            )  # fmt: skip
    db.execute(
        text(
            """UPDATE dq_quarantine SET resolution = :r, resolved_at = now(), resolution_note = :n WHERE id = :i"""
        ),
        {"r": resolution, "n": note, "i": qid},
    )
    log.info("quarantine resolved", extra={"data": {"id": qid, "resolution": resolution, "note": note}})


def nav_majority_dates(db: Session, lo: date, hi: date, known_at: datetime) -> set[date]:
    """AMFI publication days seen in our history: dates where more than half of the held schemes have a NAV."""
    rows = db.execute(
        text(
            """WITH n AS (SELECT valid_at, count(DISTINCT asset_id) c FROM prices
                          WHERE price_type = 'NAV' AND quality_status <> 'quarantined'
                            AND valid_at BETWEEN :lo AND :hi AND recorded_at <= :k GROUP BY valid_at),
                    t AS (SELECT count(DISTINCT asset_id) c FROM prices
                          WHERE price_type = 'NAV' AND quality_status <> 'quarantined'
                            AND valid_at BETWEEN :lo AND :hi AND recorded_at <= :k)
               SELECT n.valid_at FROM n, t WHERE n.c * 2 > t.c"""
        ),
        {"lo": lo, "hi": hi, "k": known_at},
    ).all()
    return {r[0] for r in rows}


def fx_observed_dates(db: Session, rate_type: str, lo: date, hi: date, known_at: datetime) -> set[date]:
    """Days a series published, quarantined rows included: a `0.00` row is still a publication day."""
    rows = db.execute(
        text(
            "SELECT DISTINCT valid_at FROM fx_rates WHERE rate_type = :t AND valid_at BETWEEN :lo AND :hi "
            "AND recorded_at <= :k"
        ),
        {"t": rate_type, "lo": lo, "hi": hi, "k": known_at},
    ).all()
    return {r[0] for r in rows}


OBSERVED_LOOKBACK_DAYS = 400  # about a year of publication days for a derived calendar to learn from


def observed_for(db: Session, market: str, rate_type: str | None, day: date, known_at: datetime) -> set[date]:
    """History for a derived calendar (AMFI, FBIL, SBI); other markets need none."""
    lo = day - timedelta(days=OBSERVED_LOOKBACK_DAYS)
    hi = max(day, known_at.date())  # what we knew by `known_at`, including days after `day` (they show gaps)
    if market == "AMFI":
        return nav_majority_dates(db, lo, hi, known_at)
    if market in ("FBIL", "SBI") and rate_type:
        return fx_observed_dates(db, rate_type, lo, hi, known_at)
    return set()
