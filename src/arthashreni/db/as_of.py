"""Bitemporal reads (E4, ADR-0008): the only way `backtest` / `research` may touch market data.

Each function returns the newest row with `valid_at <= valid_at` that was known (`recorded_at <= known_at`),
skipping quarantined rows unless asked. Results are `Measured`: stale, conflicting and missing are labelled.
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.dq.calendars import Market
from arthashreni.dq.checks import staleness
from arthashreni.dq.measured import Measured, QualityStatus, missing, worst
from arthashreni.dq.store import observed_for

FxRateType = Literal["SBI_TT_BUY", "FBIL_REF", "BROKER_ACTUAL"]
SBI_NOTE = "single unofficial archive"


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def _market(asset_class: str, mic: str | None) -> Market:
    if asset_class == "MF":
        return "AMFI"
    if mic in ("XNYS", "XNAS"):
        return "XNYS"
    if mic in ("XNSE", "XBOM"):
        return "XBOM"
    return "WEEKDAY"


def _effective_delay(delay_days: int, valid_at: date, known_day: date) -> int:
    """Older dates should be complete already, so the publication delay only matters near `known_at`."""
    return max(0, delay_days - (known_day - valid_at).days)


def _label(
    row: Any,
    *,
    value: Decimal,
    valid_at: date,
    known_at: datetime,
    market: Market,
    observed: set[date],
    conflict: tuple[Decimal, str] | None,
) -> Measured[Decimal]:
    status = QualityStatus(row.quality_status)
    notes = list(row.quality_notes)
    ref = min(valid_at, known_at.date())  # nobody can ask what the market did after the day we know about
    delay = _effective_delay(row.expected_delay_days, ref, known_at.date())
    s = staleness(row.valid_at, ref, market, delay, observed)
    status = worst(status, s.status)
    if s.note:
        notes.append(s.note)
    if conflict:
        status = worst(status, QualityStatus.CONFLICTING)
        notes.append(f"sources differ by {conflict[0]:.2%}; chose by rule: {conflict[1]}")
    return Measured(value, status, _stamp(row.valid_at), (row.source_id,), tuple(notes))


def _conflict(
    db: Session, subject: str, row_valid_at: date, row_id: int, known_at: datetime
) -> tuple[Decimal, str] | None:
    r = db.execute(
        text(
            """SELECT diff_pct, rule FROM source_conflicts WHERE subject = :s AND valid_at = :v
               AND :id = ANY(observation_ids) AND detected_at <= :k ORDER BY id DESC LIMIT 1"""
        ),
        {"s": subject, "v": row_valid_at, "id": row_id, "k": known_at},
    ).one_or_none()
    return None if r is None else (r[0], r[1])


def price_as_of(
    db: Session,
    asset_id: int,
    valid_at: date,
    known_at: datetime | None = None,
    *,
    include_quarantined: bool = False,
) -> Measured[Decimal]:
    known_at = known_at or _now()
    asset = db.execute(
        text("SELECT asset_class, exchange_mic FROM ref.assets WHERE id = :a"), {"a": asset_id}
    ).one_or_none()
    if asset is None:
        return missing(_stamp(valid_at), f"unknown asset {asset_id}")
    row = db.execute(
        text(
            """SELECT p.id, p.valid_at, p.close, p.quality_status, p.quality_notes, p.source_id, r.expected_delay_days
               FROM prices p JOIN ref.data_source_registry r ON r.id = p.source_id
               WHERE p.asset_id = :a AND p.valid_at <= :v AND p.recorded_at <= :k
                 AND (:q OR p.quality_status <> 'quarantined')
               ORDER BY p.valid_at DESC, p.recorded_at DESC, p.id DESC LIMIT 1"""
        ),
        {"a": asset_id, "v": valid_at, "k": known_at, "q": include_quarantined},
    ).one_or_none()
    if row is None:
        return missing(_stamp(valid_at), "no price observation")
    market = _market(asset[0], asset[1])
    observed = observed_for(db, market, None, valid_at, known_at)
    return _label(
        row,
        value=row.close,
        valid_at=valid_at,
        known_at=known_at,
        market=market,
        observed=observed,
        conflict=_conflict(db, f"asset:{asset_id}", row.valid_at, row.id, known_at),
    )


def fx_as_of(
    db: Session,
    rate_type: FxRateType,
    valid_at: date,
    known_at: datetime | None = None,
    *,
    pair: str = "USDINR",
    include_quarantined: bool = False,
) -> Measured[Decimal]:
    """`rate_type` is required and has no default (ADR-0016)."""
    known_at = known_at or _now()
    row = db.execute(
        text(
            """SELECT f.id, f.valid_at, f.rate, f.quality_status, f.quality_notes, f.source_id, r.expected_delay_days
               FROM fx_rates f JOIN ref.data_source_registry r ON r.id = f.source_id
               WHERE f.pair = :p AND f.rate_type = :t AND f.valid_at <= :v AND f.recorded_at <= :k
                 AND (:q OR f.quality_status <> 'quarantined')
               ORDER BY f.valid_at DESC, f.recorded_at DESC, f.id DESC LIMIT 1"""
        ),
        {"p": pair, "t": rate_type, "v": valid_at, "k": known_at, "q": include_quarantined},
    ).one_or_none()
    if row is None:
        return missing(_stamp(valid_at), f"no {rate_type} observation")
    market: Market = {"FBIL_REF": "FBIL", "SBI_TT_BUY": "SBI"}.get(rate_type, "WEEKDAY")  # type: ignore[assignment]
    notes_extra = (SBI_NOTE,) if rate_type == "SBI_TT_BUY" else ()
    m = _label(
        row,
        value=row.rate,
        valid_at=valid_at,
        known_at=known_at,
        market=market,
        observed=observed_for(db, market, rate_type, valid_at, known_at),
        conflict=_conflict(db, f"fx:{pair}", row.valid_at, row.id, known_at),
    )
    if rate_type == "SBI_TT_BUY":  # ADR-0023: never promoted without an independent second archive
        return Measured(
            m.value, worst(m.status, QualityStatus.ESTIMATED), m.as_of, m.source_ids, (*m.notes, *notes_extra)
        )
    return m


def valuation_fx_typed(
    db: Session, valid_at: date, known_at: datetime | None = None
) -> tuple[Measured[Decimal], FxRateType]:
    """ADR-0016 valuation rule: FBIL_REF, else the latest SBI TT buy marked `estimated`; the rate type used
    is returned so reports can name it."""
    primary = fx_as_of(db, "FBIL_REF", valid_at, known_at)
    if primary.value is not None:
        return primary, "FBIL_REF"
    fallback = fx_as_of(db, "SBI_TT_BUY", valid_at, known_at)
    if fallback.value is None:
        return primary, "FBIL_REF"
    return (
        Measured(
            fallback.value,
            worst(fallback.status, QualityStatus.ESTIMATED),
            fallback.as_of,
            fallback.source_ids,
            (*fallback.notes, "FBIL_REF unavailable: SBI TT buying rate used"),
        ),
        "SBI_TT_BUY",
    )


def valuation_fx(db: Session, valid_at: date, known_at: datetime | None = None) -> Measured[Decimal]:
    return valuation_fx_typed(db, valid_at, known_at)[0]


def price_series(
    db: Session, asset_id: int, start: date, end: date, known_at: datetime | None = None
) -> list[tuple[date, Measured[Decimal]]]:
    """Observed closes in [start, end] known by `known_at`: the newest row per day, each labelled with its
    own quality (quarantined rows are returned as `quarantined` so callers drop them knowingly, and rows
    that sources disagree on are `conflicting`). No gap filling: a day without an observation is absent."""
    known_at = known_at or _now()
    rows = db.execute(
        text(
            """SELECT DISTINCT ON (p.valid_at) p.id, p.valid_at, p.close, p.quality_status, p.quality_notes, p.source_id
               FROM prices p
               WHERE p.asset_id = :a AND p.valid_at BETWEEN :s AND :e AND p.recorded_at <= :k
               ORDER BY p.valid_at, p.recorded_at DESC, p.id DESC"""
        ),
        {"a": asset_id, "s": start, "e": end, "k": known_at},
    ).all()
    conflicts = db.execute(
        text(
            """SELECT valid_at, observation_ids, diff_pct, rule FROM source_conflicts
               WHERE subject = :s AND valid_at BETWEEN :st AND :e AND detected_at <= :k"""
        ),
        {"s": f"asset:{asset_id}", "st": start, "e": end, "k": known_at},
    ).all()
    out: list[tuple[date, Measured[Decimal]]] = []
    for r in rows:
        status, notes = QualityStatus(r.quality_status), list(r.quality_notes)
        for c in conflicts:
            if c.valid_at == r.valid_at and r.id in c.observation_ids:
                status = worst(status, QualityStatus.CONFLICTING)
                notes.append(f"sources differ by {c.diff_pct:.2%}; chose by rule: {c.rule}")
        out.append((r.valid_at, Measured(r.close, status, _stamp(r.valid_at), (r.source_id,), tuple(notes))))
    return out


def fundamental_as_of(
    db: Session, asset_id: int, concept: str, valid_at: date, known_at: datetime | None = None
) -> Measured[Decimal]:
    """Newest period ending on or before `valid_at`, learned by `known_at` and already filed by that day."""
    known_at = known_at or _now()
    row = db.execute(
        text(
            """SELECT f.valid_at, f.value, f.quality_status, f.quality_notes, f.source_id, f.filed_at
               FROM fundamentals f
               WHERE f.asset_id = :a AND f.concept = :c AND f.valid_at <= :v AND f.recorded_at <= :k
                 AND (f.filed_at IS NULL OR f.filed_at <= :kd) AND f.quality_status <> 'quarantined'
               ORDER BY f.valid_at DESC, f.filed_at DESC NULLS LAST, f.recorded_at DESC, f.id DESC LIMIT 1"""
        ),
        {"a": asset_id, "c": concept, "v": valid_at, "k": known_at, "kd": known_at.date()},
    ).one_or_none()
    if row is None:
        return missing(_stamp(valid_at), f"no {concept} observation")
    notes = list(row.quality_notes)
    status = QualityStatus(row.quality_status)
    age = (valid_at - row.valid_at).days
    if age > 200:  # more than two quarterly reports behind
        status = worst(status, QualityStatus.STALE)
        notes.append(f"latest period ended {row.valid_at}, {age} days before {valid_at}")
    return Measured(row.value, status, _stamp(row.valid_at), (row.source_id,), tuple(notes))
