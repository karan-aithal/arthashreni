"""Phase 6 contract: the JSON the UI renders is exactly what the API returns for the DEMO month.

`apps/web/fixtures/*.json` are the API responses the web tests render. This test regenerates them from the
live API and fails on any difference, so a page tested against a fixture is tested against the real contract.
Regenerate with `UPDATE_UI_FIXTURES=1 pytest tests/test_phase6_contract.py`."""

import json
import os
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from arthashreni.api.app import app
from arthashreni.api.auth import hash_password
from arthashreni.core.config import PROJECT_ROOT, get_settings
from arthashreni.demo import seed_demo, seed_demo_policy
from arthashreni.demo_market import DEMO_TODAY, load_demo_market

pytestmark = pytest.mark.usefixtures("ledger_db")
FIXTURES = PROJECT_ROOT / "apps" / "web" / "fixtures"
PASSWORD = "correct horse battery"  # pragma: allowlist secret
AS_OF = DEMO_TODAY.isoformat()
ENDPOINTS = {
    "portfolio": f"/portfolio?as_of={AS_OF}",
    "risk": f"/risk?as_of={AS_OF}",
    "review": f"/review?as_of={AS_OF}",
    "transactions": "/transactions",
    "imports": "/imports",
    "sources-health": "/sources/health",
    "quarantine": "/quarantine",
}


@pytest.fixture(scope="module")
def client(ledger_db: str, tmp_path_factory: pytest.TempPathFactory) -> Any:
    mp = pytest.MonkeyPatch()
    mp.setenv("ARTHA_RAW_DIR", str(tmp_path_factory.mktemp("raw")))
    mp.setenv("ARTHA_DATA_DIR", str(tmp_path_factory.mktemp("data")))
    get_settings.cache_clear()
    seed_demo()
    load_demo_market()
    seed_demo_policy()
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ref.local_user (id, username, password_hash) VALUES (1, 'demo', :h)"),
            {"h": hash_password(PASSWORD)},
        )
    c = TestClient(app)
    assert c.post("/auth/login", json={"username": "demo", "password": PASSWORD}).status_code == 200
    yield c
    engine.dispose()
    mp.undo()
    get_settings.cache_clear()


WALL_CLOCK = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+[+-]\d\d:\d\d"
)  # microseconds: "now", not a data date
MASKED = "2026-11-21T00:00:00+00:00"


def _mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _mask(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v) for v in obj]
    if isinstance(obj, str) and WALL_CLOCK.fullmatch(obj):
        return MASKED
    return obj


def _stable(obj: Any) -> Any:
    """Round-trip through JSON (what a browser receives) with run-time clock stamps masked."""
    return _mask(json.loads(json.dumps(obj, sort_keys=True)))


@pytest.mark.parametrize("name", ENDPOINTS)
def test_ui_fixture_matches_the_api(client: TestClient, name: str) -> None:
    r = client.get(ENDPOINTS[name])
    assert r.status_code == 200, r.text
    body = _stable(r.json())
    path = FIXTURES / f"{name}.json"
    if os.environ.get("UPDATE_UI_FIXTURES"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    assert path.is_file(), f"missing fixture {path.name}; run with UPDATE_UI_FIXTURES=1"
    assert body == json.loads(path.read_text()), f"{name}: API output changed; regenerate the UI fixtures"


def test_the_ui_endpoints_are_read_only(client: TestClient) -> None:
    for path in ("/portfolio", "/risk", "/review", "/transactions", "/imports"):
        assert client.post(path).status_code in (403, 405)  # no CSRF token, and no POST handler anyway
