"""Run one adapter end to end: consent, fetch, store the raw payload, validate, check, store, cross-check.

Outcomes (D3, E3): a provider outage is recorded and queued, a schema change quarantines the whole run
and stores nothing from it, an impossible value or unexplained jump is stored as `quarantined` and queued,
disagreeing sources are both kept and flagged. One bad source never stops the others.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.core.config import Dataset, get_settings
from arthashreni.db.session import session_scope
from arthashreni.dq import store
from arthashreni.dq.checks import CheckResult, diff_pct, impossible_value, jump
from arthashreni.dq.measured import QualityStatus
from arthashreni.dq.params import (
    DISAGREE_FX_PCT,
    DISAGREE_PRICE_PCT,
    JUMP_WINDOW,
)
from arthashreni.ingestion.sources.contract import (
    Adapter,
    FundamentalObs,
    FxObs,
    NotConsented,
    Observation,
    PriceObs,
    RawPayload,
    SchemaDrift,
    SourceUnavailable,
    Window,
)
from arthashreni.ledger.assets import UnknownAsset, resolve_asset

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    source_id: str
    status: str  # OK, FAILED, SCHEMA_DRIFT, SKIPPED
    stored: int = 0
    duplicates: int = 0
    quarantined: int = 0
    conflicts: int = 0
    unknown_assets: int = 0
    detail: str = ""
    quarantine_ids: list[int] = field(default_factory=list)


def check_consent(source_id: str, *, offline: bool = False) -> None:
    """Invariant 10: contacting a source reveals what the investor follows, so it needs recorded consent."""
    if not offline and source_id not in get_settings().consented():
        raise NotConsented(f"no consent recorded for {source_id}: add it to ARTHA_CONSENTED_SOURCES")


def save_raw(payload: RawPayload, raw_dir: Path) -> str:
    folder = raw_dir / payload.source_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{payload.fetched_at:%Y-%m-%d}-{payload.sha256[:12]}.{payload.suffix}"
    if not path.exists():
        path.write_bytes(payload.body)
        path.chmod(0o600)
    return str(path)


def _mark(db: Session, source_id: str, ok: bool) -> None:
    col = "last_success_at" if ok else "last_failure_at"
    db.execute(text(f"UPDATE ref.data_source_registry SET {col} = now() WHERE id = :s"), {"s": source_id})  # noqa: S608


def record_call(dataset: Dataset, source_id: str, ok: bool, status: int | None, detail: str) -> None:
    """Autonomous transaction so call history survives a rolled-back run."""
    with session_scope(dataset) as db:
        db.execute(
            text(
                "INSERT INTO ref.source_calls (source_id, dataset, ok, http_status, detail) "
                "VALUES (:s, :d, :ok, :h, :detail)"
            ),
            {"s": source_id, "d": dataset, "ok": ok, "h": status, "detail": detail},
        )


def _asset_id(db: Session, dataset: Dataset, ref: str) -> int | None:
    if ref.upper().startswith("CIK:"):
        row = db.execute(
            text("SELECT id FROM ref.assets WHERE cik = :c AND is_demo = :d"),
            {"c": ref[4:].lstrip("0") or "0", "d": dataset == "demo"},
        ).first()
        if row is None:  # stored without padding; also try padded form
            row = db.execute(
                text("SELECT id FROM ref.assets WHERE lpad(cik, 10, '0') = :c AND is_demo = :d"),
                {"c": ref[4:], "d": dataset == "demo"},
            ).first()
        return None if row is None else int(row[0])
    try:
        return resolve_asset(db, dataset, ref).id
    except UnknownAsset:
        return None


def _action_dates(db: Session, asset_id: int) -> list[date]:
    rows = db.execute(
        text("SELECT ex_date FROM corporate_actions WHERE asset_id = :a"), {"a": asset_id}
    ).all()
    return [r[0] for r in rows]


def _verdict(db: Session, obs: PriceObs | FxObs, asset_id: int | None) -> tuple[CheckResult, str | None]:
    """Run the value-level E3 checks: impossible state first, then unexplained jump."""
    if isinstance(obs, PriceObs):
        kind, value = ("nav" if obs.price_type == "NAV" else "price"), obs.close
    else:
        kind, value = "fx", obs.rate
    bad = impossible_value("NAV" if kind == "nav" else "FX rate" if kind == "fx" else "price", value)
    if bad.status is QualityStatus.QUARANTINED:
        return bad, "impossible_state"
    n = JUMP_WINDOW + 1
    if isinstance(obs, PriceObs):
        assert asset_id is not None
        hist = store.price_history(db, asset_id, obs.valid_at, n)
        actions = _action_dates(db, asset_id)
        if obs.split_factor not in (None, Decimal(1)):
            actions.append(obs.valid_at)
    else:
        hist = store.fx_history(db, obs.rate_type, obs.valid_at, n)
        actions = []
    res = jump(hist, value, kind, action_dates=actions, on=obs.valid_at)
    return res, ("unexplained_jump" if res.status is QualityStatus.QUARANTINED else None)


def _exists_price(db: Session, asset_id: int, obs: PriceObs, source_id: str) -> tuple[bool, Decimal | None]:
    rows = db.execute(
        text(
            "SELECT close FROM prices WHERE asset_id = :a AND valid_at = :v AND source_id = :s "
            "AND price_type = :t ORDER BY recorded_at DESC, id DESC"
        ),
        {"a": asset_id, "v": obs.valid_at, "s": source_id, "t": obs.price_type},
    ).all()
    if not rows:
        return False, None
    return rows[0][0] == obs.close, rows[0][0]


def _exists_fx(db: Session, obs: FxObs, source_id: str) -> tuple[bool, Decimal | None]:
    rows = db.execute(
        text(
            "SELECT rate FROM fx_rates WHERE rate_type = :t AND valid_at = :v AND source_id = :s "
            "AND provider = :p ORDER BY recorded_at DESC, id DESC"
        ),
        {"t": obs.rate_type, "v": obs.valid_at, "s": source_id, "p": obs.provider},
    ).all()
    if not rows:
        return False, None
    return rows[0][0] == obs.rate, rows[0][0]


def _store_batch(
    db: Session,
    dataset: Dataset,
    source_id: str,
    payload: RawPayload,
    observations: list[Observation],
    raw_id: int,
    result: RunResult,
) -> list[tuple[str, int, Decimal, date]]:
    """Insert observations. Returns (kind, row id, value, valid_at) of accepted rows, for cross-checks."""
    placed: list[tuple[str, int, Decimal, date]] = []
    for obs in sorted(observations, key=lambda o: o.valid_at):
        asset_id: int | None = None
        if not isinstance(obs, FxObs):
            asset_id = _asset_id(db, dataset, obs.asset_ref)
            if asset_id is None:
                result.unknown_assets += 1
                continue
        if isinstance(obs, FundamentalObs):
            assert asset_id is not None
            fid = store.insert_fundamental(
                db,
                asset_id=asset_id,
                concept=obs.concept,
                valid_at=obs.valid_at,
                unit=obs.unit,
                value=obs.value,
                accession_no=obs.accession_no,
                source_id=source_id,
                fiscal_year=obs.fiscal_year,
                fiscal_period=obs.fiscal_period,
                form=obs.form,
                filed_at=obs.filed_at,
                raw_payload_id=raw_id,
            )
            if fid is None:
                result.duplicates += 1
            else:
                result.stored += 1
            continue
        same, previous = (
            _exists_fx(db, obs, source_id)
            if isinstance(obs, FxObs)
            else _exists_price(db, asset_id or 0, obs, source_id)
        )
        if same:
            result.duplicates += 1
            continue
        verdict, check = _verdict(db, obs, asset_id)
        status = verdict.status if verdict.status is QualityStatus.QUARANTINED else QualityStatus.OK
        notes = [verdict.note] if verdict.note else []
        if previous is not None:
            notes.append(f"restates earlier value {previous}")
        if isinstance(obs, FxObs):
            row_id = store.insert_fx(
                db,
                rate_type=obs.rate_type,
                underlying_series=obs.underlying_series,
                provider=obs.provider,
                valid_at=obs.valid_at,
                rate=obs.rate,
                source_id=source_id,
                observed_at=obs.observed_at,
                pair=obs.pair,
                status=status,
                notes=notes,
                raw_payload_id=raw_id,
            )
            table, value = "fx_rates", obs.rate
        else:
            assert asset_id is not None
            row_id = store.insert_price(
                db,
                asset_id=asset_id,
                valid_at=obs.valid_at,
                close=obs.close,
                currency=obs.currency,
                price_type=obs.price_type,
                source_id=source_id,
                status=status,
                notes=notes,
                raw_payload_id=raw_id,
                split_factor=obs.split_factor,
            )
            table, value = "prices", obs.close
        result.stored += 1
        if status is QualityStatus.QUARANTINED and check:
            qid = store.quarantine(
                db,
                table,
                str(row_id),
                check,
                {"obs": obs.model_dump(mode="json"), "reason": verdict.note},
                source_id,
            )
            result.quarantined += 1
            if qid is not None:
                result.quarantine_ids.append(qid)
        else:
            placed.append(("fx" if table == "fx_rates" else "price", row_id, value, obs.valid_at))
    return placed


def _cross_check_fx(db: Session, dates: set[date], result: RunResult) -> None:
    """FBIL reference vs SBI TT buying on the same date (D3: keep both, pick one by rule, flag the gap)."""
    for d in sorted(dates):
        rows = db.execute(
            text(
                """SELECT DISTINCT ON (rate_type) rate_type, id, rate FROM fx_rates
                   WHERE valid_at = :d AND rate_type IN ('FBIL_REF', 'SBI_TT_BUY')
                     AND quality_status <> 'quarantined'
                   ORDER BY rate_type, recorded_at DESC, id DESC"""
            ),
            {"d": d},
        ).all()
        by = {r[0]: r for r in rows}
        if len(by) != 2:
            continue
        diff = diff_pct(by["SBI_TT_BUY"][2], by["FBIL_REF"][2])
        if diff > DISAGREE_FX_PCT:
            store.record_conflict(
                db,
                "fx:USDINR",
                d,
                [by["FBIL_REF"][1], by["SBI_TT_BUY"][1]],
                by["FBIL_REF"][1],
                "FBIL_REF is the reference for analytics (ADR-0016); the SBI TT row is flagged",
                diff,
            )
            result.conflicts += 1


def _cross_check_prices(db: Session, placed: list[tuple[str, int, Decimal, date]], result: RunResult) -> None:
    """Fetched price vs the price on the broker/CAS trade of the same day (E3: source disagreement)."""
    for kind, row_id, value, d in placed:
        if kind != "price":
            continue
        row = db.execute(
            text(
                """SELECT p.asset_id, t.price FROM prices p JOIN transactions_effective t
                       ON t.asset_id = p.asset_id AND t.trade_date = p.valid_at
                   WHERE p.id = :id AND t.price IS NOT NULL AND t.price > 0
                     AND t.effective_type IN ('BUY', 'SELL', 'SIP', 'REDEMPTION') LIMIT 1"""
            ),
            {"id": row_id},
        ).one_or_none()
        if row is None:
            continue
        diff = diff_pct(value, row[1])
        if diff > DISAGREE_PRICE_PCT:
            store.record_conflict(
                db,
                f"asset:{row[0]}",
                d,
                [row_id],
                row_id,
                f"statement price {row[1]} stays in the ledger; the fetched price is flagged",
                diff,
            )
            result.conflicts += 1


def run_adapter(
    dataset: Dataset, adapter: Adapter, window: Window, *, offline: bool = False, raw_dir: Path | None = None
) -> RunResult:
    sid = adapter.source_id
    result = RunResult(sid, "OK")
    raw_dir = raw_dir or get_settings().raw_dir
    try:
        check_consent(sid, offline=offline)
        payload = adapter.fetch(window)
    except NotConsented as exc:
        return RunResult(sid, "SKIPPED", detail=str(exc))
    except SourceUnavailable as exc:
        return _failed(dataset, sid, "FAILED", "provider_outage", str(exc), window)
    except SchemaDrift as exc:
        return _failed(dataset, sid, "SCHEMA_DRIFT", "schema_drift", str(exc), window)

    try:
        observations = adapter.parse(payload)
    except SchemaDrift as exc:
        path = save_raw(payload, raw_dir)
        return _failed(dataset, sid, "SCHEMA_DRIFT", "schema_drift", str(exc), window, payload, path)

    path = save_raw(payload, raw_dir)
    with session_scope(dataset) as db:
        raw_id = store.insert_raw_payload(
            db,
            source_id=sid,
            sha256=payload.sha256,
            window_start=window.start,
            window_end=window.end,
            schema_ok=True,
            file_path=path,
            http_status=payload.http_status,
        )
        placed = _store_batch(db, dataset, sid, payload, observations, raw_id, result)
        _cross_check_fx(db, {d for k, _, _, d in placed if k == "fx"}, result)
        _cross_check_prices(db, placed, result)
        _mark(db, sid, True)
        store.clear_pending(db, "data_source_registry", sid, "provider_outage", "source answered again")
    log.info(
        "ingest ok",
        extra={"data": {"source": sid, "stored": result.stored, "quarantined": result.quarantined}},
    )
    return result


def _failed(
    dataset: Dataset,
    sid: str,
    status: str,
    check: str,
    message: str,
    window: Window,
    payload: RawPayload | None = None,
    path: str | None = None,
) -> RunResult:
    """One alert per cause: de-duplicated while the item is still pending."""
    with session_scope(dataset) as db:
        if payload is not None or status == "SCHEMA_DRIFT":
            sha = payload.sha256 if payload else f"{sid}:{window.end}"
            raw_id = store.insert_raw_payload(
                db,
                source_id=sid,
                sha256=sha,
                window_start=window.start,
                window_end=window.end,
                schema_ok=False,
                file_path=path,
                http_status=payload.http_status if payload else None,
                detail=message[:500],
            )
            qid = store.quarantine(db, "raw_payloads", str(raw_id), check, {"error": message}, sid)
        else:
            qid = store.quarantine(db, "data_source_registry", sid, check, {"error": message}, sid)
        _mark(db, sid, False)
    log.error("ingest failed", extra={"data": {"source": sid, "status": status, "error": message[:200]}})
    return RunResult(sid, status, detail=message, quarantine_ids=[] if qid is None else [qid])
