"""Source health (D5) and the E3 `ledger vs statement` check. A failing source never blocks the rest."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from arthashreni.dq.calendars import Market
from arthashreni.dq.checks import staleness
from arthashreni.dq.measured import QualityStatus
from arthashreni.dq.store import observed_for
from arthashreni.ledger.reconciliation import open_differences

# source -> (table with its observations, market whose calendar sets the expected date)
FRESHNESS: dict[str, tuple[str, Market, str | None]] = {
    "amfi_nav": ("prices", "AMFI", None),
    "tiingo_eod": ("prices", "XNYS", None),
    "sbi_tt_archive": ("fx_rates", "SBI", "SBI_TT_BUY"),
    "fbil_ref": ("fx_rates", "FBIL", "FBIL_REF"),
    "fbil_manual": ("fx_rates", "FBIL", "FBIL_REF"),
}
# `manual_prices` has no publication schedule (it carries any asset's prices): its freshness is judged per
# asset by `db.as_of`, not per source.
NETWORK_SOURCES = {"amfi_nav", "sbi_tt_archive", "fbil_ref", "tiingo_eod", "sec_companyfacts"}
# Fundamentals change quarterly, so they are judged by the age of the last fetch, not a calendar.
FUNDAMENTALS_MAX_AGE_DAYS = 30


@dataclass(frozen=True)
class SourceHealth:
    id: str
    name: str
    enabled: bool
    consented: bool
    state: str  # ok, stale, failing, no_data, manual, not_automated, disabled, not_consented
    freshness: QualityStatus | None
    freshness_note: str | None
    latest_valid_at: date | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    calls_today: int
    daily_quota: int | None
    pending_quarantine: int


@dataclass(frozen=True)
class HealthReport:
    sources: list[SourceHealth]
    pending_quarantine: int
    ledger_differences: int

    @property
    def blocks_monthly_review(self) -> bool:
        """Unexplained ledger differences or unreviewed quarantine items block the monthly review (C4, E3)."""
        return self.ledger_differences > 0 or self.pending_quarantine > 0


def _latest(db: Session, source_id: str, table: str) -> date | None:
    r = db.execute(
        text(
            f"SELECT max(valid_at) FROM {table} WHERE source_id = :s AND quality_status <> 'quarantined'"  # noqa: S608
        ),
        {"s": source_id},
    ).scalar_one()
    return r  # type: ignore[no-any-return]


def source_health(db: Session, today: date, consented: set[str]) -> HealthReport:
    regs = db.execute(
        text(
            """SELECT id, name, enabled, adapter, expected_delay_days, daily_quota, last_success_at,
                      last_failure_at FROM ref.data_source_registry ORDER BY priority, id"""
        )
    ).all()
    calls: dict[str, int] = dict(
        db.execute(
            text(
                "SELECT source_id, count(*) FROM ref.source_calls "
                "WHERE called_at >= date_trunc('day', now()) GROUP BY source_id"
            )
        ).all()
    )
    pending: dict[str, int] = dict(
        db.execute(
            text(
                "SELECT source_id, count(*) FROM dq_quarantine WHERE resolution = 'PENDING' "
                "AND source_id IS NOT NULL GROUP BY source_id"
            )
        ).all()
    )
    total_pending = int(
        db.execute(text("SELECT count(*) FROM dq_quarantine WHERE resolution = 'PENDING'")).scalar_one()
    )
    out: list[SourceHealth] = []
    for r in regs:
        latest: date | None = None
        fresh: QualityStatus | None = None
        note: str | None = None
        if r.id in FRESHNESS:
            table, market, rate_type = FRESHNESS[r.id]
            latest = _latest(db, r.id, table)
            if latest is not None or r.last_success_at is not None:
                observed = observed_for(db, market, rate_type, today, datetime.now(UTC))
                res = staleness(latest, today, market, r.expected_delay_days, observed)
                fresh, note = res.status, res.note
        elif r.id == "sec_companyfacts":
            latest = _latest(db, r.id, "fundamentals")
            if r.last_success_at is not None:
                age = (datetime.now(UTC) - r.last_success_at).days
                fresh = QualityStatus.OK if age <= FUNDAMENTALS_MAX_AGE_DAYS else QualityStatus.STALE
                note = None if fresh is QualityStatus.OK else f"last fetch {age} days ago"
        failing = r.last_failure_at is not None and (
            r.last_success_at is None or r.last_failure_at > r.last_success_at
        )
        if r.adapter is None:
            state = "not_automated"
        elif r.adapter.startswith("manual"):
            state = "manual" if latest or r.last_success_at else "no_data"
        elif not r.enabled:
            state = "disabled"
        elif r.id in NETWORK_SOURCES and r.id not in consented:
            state = "not_consented"
        elif failing:
            state = "failing"
        elif fresh is None:
            state = "no_data"
        else:
            state = "ok" if fresh is QualityStatus.OK else "stale"
        out.append(
            SourceHealth(r.id, r.name, r.enabled, r.id in consented, state, fresh, note, latest,
                         r.last_success_at, r.last_failure_at, int(calls.get(r.id, 0)), r.daily_quota,
                         int(pending.get(r.id, 0)))
        )  # fmt: skip
    return HealthReport(out, total_pending, len(open_differences(db)))
