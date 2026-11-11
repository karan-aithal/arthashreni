"""Engines and sessions bound to one dataset schema (ADR-0004).

The demo engine logs in as artha_demo, the real engine as artha_real, so the
database itself prevents cross-dataset access. search_path is fixed per engine.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from arthashreni.core.config import Dataset, get_settings
from arthashreni.core.gates import gate_approved


class RealDataLocked(RuntimeError):
    """REAL dataset may not be used before the Phase 4 gate is approved."""


def _check_dataset(dataset: Dataset) -> None:
    if dataset not in ("demo", "real"):
        raise ValueError(f"unknown dataset {dataset!r}")
    if dataset == "real" and not gate_approved(4):
        raise RealDataLocked("REAL dataset is locked until docs/gates/phase-4.approved exists")


@lru_cache
def get_engine(dataset: Dataset) -> Engine:
    _check_dataset(dataset)
    engine = create_engine(get_settings().app_url(dataset), pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_conn: Any, _record: Any) -> None:
        with dbapi_conn.cursor() as cur:
            cur.execute(f"SET search_path TO {dataset}, ref")
        dbapi_conn.commit()

    return engine


@contextmanager
def session_scope(dataset: Dataset | None = None) -> Iterator[Session]:
    ds = dataset or get_settings().dataset
    factory = sessionmaker(get_engine(ds), expire_on_commit=False)
    with factory() as session, session.begin():
        yield session
