"""Scheduled jobs (G3, ADR-0019): APScheduler, Asia/Kolkata, run history in `ref.job_runs`, catch-up on start.

`UNIQUE(job_name, scheduled_for)` makes every slot run at most once. APScheduler's own misfire handling is
not trusted: at start-up `catch_up` reads the history and runs what was missed.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore  # type: ignore[import-untyped]
from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-untyped]
from sqlalchemy import text

from arthashreni.core.config import Dataset, get_settings
from arthashreni.db.session import get_engine, session_scope
from arthashreni.ingestion.sources.ingest import run_daily

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
MAX_CATCH_UP_DAYS = 7  # beyond this the adapters' own look-back windows no longer cover the gap


def job_name(dataset: Dataset) -> str:
    return f"ingest:{dataset}"


def _slot(day: date) -> datetime:
    h, m = (int(x) for x in get_settings().ingest_time.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=IST)


def run_slot(
    dataset: Dataset, scheduled_for: datetime, *, coalesced_into: datetime | None = None, **kw: Any
) -> str:
    """Run one slot idempotently. Returns OK, PARTIAL, FAILED, or SKIPPED when the slot already ran."""
    name = job_name(dataset)
    with session_scope(dataset) as db:
        claimed = db.execute(
            text(
                "INSERT INTO ref.job_runs (job_name, scheduled_for, status) VALUES (:n, :s, 'RUNNING') "
                "ON CONFLICT (job_name, scheduled_for) DO NOTHING RETURNING id"
            ),
            {"n": name, "s": scheduled_for},
        ).scalar_one_or_none()
    if claimed is None:
        return "SKIPPED"
    if coalesced_into is not None:
        _finish(dataset, claimed, "OK", {"coalesced_into": coalesced_into.isoformat()})
        return "OK"
    try:
        results = run_daily(dataset, scheduled_for.astimezone(IST).date(), **kw)
    except Exception as exc:  # the job record must say what happened, then the error is re-raised to the log
        _finish(dataset, claimed, "FAILED", {"error": str(exc)[:300]})
        log.exception("ingest job crashed")
        return "FAILED"
    bad = [r for r in results if r.status in ("FAILED", "SCHEMA_DRIFT")]
    ran = [r for r in results if r.status != "SKIPPED"]
    status = "OK" if not bad else "FAILED" if len(bad) == len(ran) else "PARTIAL"
    _finish(
        dataset, claimed, status,
        {"sources": {r.source_id: {"status": r.status, "stored": r.stored, "quarantined": r.quarantined,
                                   "detail": r.detail[:200]} for r in results}},
    )  # fmt: skip
    return status


def _finish(dataset: Dataset, run_id: int, status: str, summary: dict[str, Any]) -> None:
    import json

    with session_scope(dataset) as db:
        db.execute(
            text(
                "UPDATE ref.job_runs SET status = :st, finished_at = now(), summary = CAST(:sm AS jsonb) "
                "WHERE id = :i"
            ),
            {"st": status, "sm": json.dumps(summary), "i": run_id},
        )


def missed_slots(dataset: Dataset, now: datetime) -> list[datetime]:
    """Daily slots since the last finished run (at most `MAX_CATCH_UP_DAYS`) that have no run recorded."""
    with session_scope(dataset) as db:
        done = {
            r[0]
            for r in db.execute(
                text("SELECT scheduled_for FROM ref.job_runs WHERE job_name = :n"), {"n": job_name(dataset)}
            ).all()
        }
    today = now.astimezone(IST).date()
    slots = [_slot(today - timedelta(days=i)) for i in range(MAX_CATCH_UP_DAYS, -1, -1)]
    return [s for s in slots if s <= now and s not in done]


def catch_up(dataset: Dataset, now: datetime | None = None, **kw: Any) -> list[tuple[datetime, str]]:
    """Run missed slots in order. Sources fetch look-back windows rather than single days, so only the
    newest missed slot fetches; earlier ones are recorded as coalesced into it (one fetch, no hammering)."""
    now = now or datetime.now(UTC)
    slots = missed_slots(dataset, now)
    out: list[tuple[datetime, str]] = []
    for s in slots:
        newest = s is slots[-1]
        out.append((s, run_slot(dataset, s, coalesced_into=None if newest else slots[-1], **kw)))
    return out


def scheduled_ingest(dataset: Dataset) -> None:
    """The APScheduler entry point (must stay a module-level function: jobs are stored in the database)."""
    run_slot(dataset, _slot(datetime.now(IST).date()))


def build_scheduler(dataset: Dataset) -> BackgroundScheduler:
    h, m = (int(x) for x in get_settings().ingest_time.split(":"))
    sched = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=get_engine(dataset), tablename="apscheduler_jobs",
                                                 tableschema="ref")},
        timezone=IST,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": MAX_CATCH_UP_DAYS * 86400},
    )  # fmt: skip
    sched.add_job(
        "arthashreni.jobs:scheduled_ingest", "cron", hour=h, minute=m, args=[dataset],
        id=job_name(dataset), replace_existing=True,
    )  # fmt: skip
    return sched
