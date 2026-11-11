"""Source health and the quarantine queue (read-only). Resolving items stays in the CLI: it needs a reason."""

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from arthashreni.api.auth import RequireSession
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.dq.health import source_health
from arthashreni.dq.store import list_quarantine

router = APIRouter(tags=["data quality"])


class SourceOut(BaseModel):
    id: str
    name: str
    state: str
    enabled: bool
    consented: bool
    freshness: str | None
    freshness_note: str | None
    latest_valid_at: date | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    calls_today: int
    daily_quota: int | None
    pending_quarantine: int


class HealthOut(BaseModel):
    dataset: str
    sources: list[SourceOut]
    pending_quarantine: int
    ledger_differences: int
    blocks_monthly_review: bool


@router.get("/sources/health")
def sources_health(_: RequireSession) -> HealthOut:
    s = get_settings()
    with session_scope() as db:
        rep = source_health(db, date.today(), s.consented())
    return HealthOut(
        dataset=s.dataset.upper(),
        sources=[
            SourceOut(**{**h.__dict__, "freshness": h.freshness.value if h.freshness else None})
            for h in rep.sources
        ],
        pending_quarantine=rep.pending_quarantine,
        ledger_differences=rep.ledger_differences,
        blocks_monthly_review=rep.blocks_monthly_review,
    )


@router.get("/quarantine")
def quarantine_queue(_: RequireSession, pending_only: bool = True) -> list[dict[str, Any]]:
    with session_scope() as db:
        return list_quarantine(db, pending_only=pending_only)
