"""Risk and monthly review (read-only). Nothing here stores, approves or executes anything; it shows the
recommendation the engines produce, with every number carrying its status and as-of."""

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from arthashreni.api.auth import RequireSession
from arthashreni.api.serialize import jsonable
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope
from arthashreni.policy import store as pstore
from arthashreni.review import monthly_review, run_risk

router = APIRouter(tags=["risk and review"])


@router.get("/risk")
def risk(_: RequireSession, as_of: date | None = None) -> dict[str, Any]:
    """Signals, the four-level rating, stress scenarios (not predictions) and the alerts that would be new."""
    dataset = get_settings().dataset
    with session_scope() as db:
        run = run_risk(db, dataset, as_of or date.today())
    out: dict[str, Any] = jsonable(run)
    out["dataset"] = dataset.upper()
    return out


@router.get("/review")
def review(_: RequireSession, period: date | None = None, as_of: date | None = None) -> dict[str, Any]:
    """The stored recommendation for the month if one exists, otherwise the one the engines would produce now.
    `human_approval_required` is always true."""
    dataset = get_settings().dataset
    today = as_of or date.today()
    month = (period or today).replace(day=1)
    with session_scope() as db:
        stored = pstore.load_recommendation(db, month)
        if stored is not None:
            return {
                "dataset": dataset.upper(),
                "stored": True,
                "recommendation": stored[1].model_dump(mode="json"),
            }
        try:
            fresh = monthly_review(db, dataset, month, today)
        except pstore.PolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "dataset": dataset.upper(),
        "stored": False,
        "recommendation": fresh.recommendation.model_dump(mode="json"),
    }
