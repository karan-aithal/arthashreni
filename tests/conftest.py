"""Test setup: a fresh throwaway database `arthashreni_test`, migrated to head.

Uses the role credentials from .env but never touches the main database.
"""

import os
import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import make_url

from arthashreni.core.config import Settings, get_settings
from arthashreni.db.session import get_engine

TEST_DB = "arthashreni_test"


def _swap_db(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session", autouse=True)
def test_database() -> Iterator[None]:
    base = Settings()
    from arthashreni.backup import PgTarget, _recreate_db, drop_db

    owner = PgTarget.from_sqlalchemy(base.db_owner_url.get_secret_value())
    _recreate_db(owner, TEST_DB)
    env = {
        "ARTHA_DB_OWNER_URL": _swap_db(base.db_owner_url.get_secret_value(), TEST_DB),
        "ARTHA_DB_DEMO_URL": _swap_db(base.db_demo_url.get_secret_value(), TEST_DB),
        "ARTHA_DB_REAL_URL": _swap_db(base.db_real_url.get_secret_value(), TEST_DB),
        "ARTHA_DATASET": "demo",
        "ARTHA_RESTORE_TEST_DB": "arthashreni_test_restore",
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    get_settings.cache_clear()
    get_engine.cache_clear()
    subprocess.run(
        ["alembic", "upgrade", "head"],
        env={**os.environ, "ARTHA_MIGRATE_URL": env["ARTHA_DB_OWNER_URL"]},
        check=True,
        capture_output=True,
    )
    yield
    for engine_ds in ("demo",):
        get_engine(engine_ds).dispose()
    get_engine.cache_clear()
    drop_db(owner, TEST_DB)
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()
