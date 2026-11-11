"""Test databases.

A session-wide database `arthashreni_test` backs the Phase 1 tests. Ledger tests need clean state
(the ledger is append-only, so tests cannot clean up), so each DB-heavy module requests the
`ledger_db` fixture and gets its own throwaway database. None of these touch `arthashreni`.
"""

import os
import re
import subprocess
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import make_url

from arthashreni.backup import PgTarget, _recreate_db, drop_db
from arthashreni.core.config import get_settings
from arthashreni.db.session import dispose_engines

URL_VARS = ("ARTHA_DB_OWNER_URL", "ARTHA_DB_DEMO_URL", "ARTHA_DB_REAL_URL")


def _swap_db(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def _use_database(name: str) -> dict[str, str | None]:
    s = get_settings()
    owner = PgTarget.from_sqlalchemy(s.db_owner_url.get_secret_value())
    _recreate_db(owner, name)
    urls = {
        "ARTHA_DB_OWNER_URL": s.db_owner_url.get_secret_value(),
        "ARTHA_DB_DEMO_URL": s.db_demo_url.get_secret_value(),
        "ARTHA_DB_REAL_URL": s.db_real_url.get_secret_value(),
    }
    env = {k: _swap_db(v, name) for k, v in urls.items()}
    env["ARTHA_DATASET"] = "demo"
    env["ARTHA_RESTORE_TEST_DB"] = f"{name}_restore"
    old = {k: os.environ.get(k) for k in env}
    dispose_engines()
    os.environ.update(env)
    get_settings.cache_clear()
    subprocess.run(
        ["alembic", "upgrade", "head"],
        env={**os.environ, "ARTHA_MIGRATE_URL": env["ARTHA_DB_OWNER_URL"]},
        check=True,
        capture_output=True,
    )
    return old


def _release_database(name: str, old: dict[str, str | None]) -> None:
    admin = PgTarget.from_sqlalchemy(get_settings().db_owner_url.get_secret_value()).with_db("postgres")
    dispose_engines()
    drop_db(admin, name)
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


# Modules that never touch the database. They skip the throwaway database, which is what lets mutation
# testing run them in parallel without workers recreating the same database under each other.
NO_DB_MODULES = frozenset(
    {
        "test_xirr",
        "test_analytics",
        "test_analytics_exact",
        "test_analytics_properties",
        "test_lots",
        "test_ledger_exact",
        "test_ledger_properties",
    }
)


@pytest.fixture(scope="session")
def test_database() -> Iterator[None]:
    old = _use_database("arthashreni_test")
    yield
    _release_database("arthashreni_test", old)


@pytest.fixture(autouse=True)
def _database_unless_pure(request: pytest.FixtureRequest) -> None:
    if request.module.__name__.rsplit(".", 1)[-1] not in NO_DB_MODULES:
        request.getfixturevalue("test_database")


@pytest.fixture(scope="module")
def ledger_db(request: pytest.FixtureRequest, test_database: None) -> Iterator[str]:
    """A fresh migrated database for one test module."""
    name = "arthashreni_t_" + re.sub(r"\W", "", request.module.__name__.rsplit(".", 1)[-1])[:30]
    old = _use_database(name)
    yield name
    _release_database(name, old)


# ---- hypothesis profiles: `dev` for normal runs, `ci` (>= 1000 examples) for `make verify` ----------
from hypothesis import HealthCheck, settings  # noqa: E402

settings.register_profile("dev", max_examples=150, deadline=None)
settings.register_profile(
    "ci", max_examples=1000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """CLI tests call configure_logging(), which binds a handler to the runner's captured stderr."""
    import logging

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
