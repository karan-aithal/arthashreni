"""Portfolio report (read-only). Everything is a `Measured`: value, status, as-of, sources, notes."""

from datetime import date
from typing import Any

from fastapi import APIRouter

from arthashreni.api.auth import RequireSession
from arthashreni.api.serialize import jsonable
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.portfolio import build_report

router = APIRouter(tags=["portfolio"])


@router.get("/portfolio")
def portfolio(_: RequireSession, as_of: date | None = None) -> dict[str, Any]:
    """Valuation, P&L, returns (XIRR, TWR, absolute), FX attribution, allocation, concentration and
    series statistics for the current dataset. Prices and rates are the newest known on `as_of`."""
    dataset = get_settings().dataset
    with session_scope() as db:
        report = build_report(db, dataset, as_of or date.today())
    out: dict[str, Any] = jsonable(report)
    out["dataset"] = dataset.upper()
    return out
