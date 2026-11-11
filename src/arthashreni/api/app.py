"""FastAPI application. Thin layer; binds to localhost only (ADR-0012)."""

import logging

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from arthashreni import __version__
from arthashreni.api.auth import router as auth_router
from arthashreni.core.config import get_settings
from arthashreni.db.session import session_scope

log = logging.getLogger(__name__)

app = FastAPI(title="ArthaShreni API", version=__version__)
app.include_router(auth_router)


class Health(BaseModel):
    status: str
    dataset: str
    database: str
    version: str


@app.get("/health")
def health() -> Health:
    dataset = get_settings().dataset
    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # health must report, not crash
        log.warning("health: database unavailable", extra={"data": {"error": str(exc)}})
        database = "unavailable"
    return Health(
        status="ok" if database == "ok" else "degraded",
        dataset=dataset.upper(),
        database=database,
        version=__version__,
    )
