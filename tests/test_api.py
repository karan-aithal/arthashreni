from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from arthashreni.api.app import app
from arthashreni.api.auth import _token_hash, hash_password
from arthashreni.core.config import get_settings

# pragma: allowlist nextline secret
PASSWORD = "correct horse battery"


@pytest.fixture(scope="module", autouse=True)
def local_user() -> Iterator[None]:
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ref.local_user (id, username, password_hash) VALUES (1, 'karan', :h)"),
            {"h": hash_password(PASSWORD)},
        )
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ref.auth_sessions"))
        conn.execute(text("DELETE FROM ref.local_user"))
    engine.dispose()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "dataset": "DEMO", "database": "ok", "version": "0.1.0"}


def test_wrong_password_rejected(client: TestClient) -> None:
    # pragma: allowlist nextline secret
    assert client.post("/auth/login", json={"username": "karan", "password": "nope"}).status_code == 401
    assert client.post("/auth/login", json={"username": "other", "password": PASSWORD}).status_code == 401


def test_me_requires_login(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401


def test_login_cookie_csrf_logout(client: TestClient) -> None:
    r = client.post("/auth/login", json={"username": "karan", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    csrf = r.json()["csrf_token"]

    assert client.get("/auth/me").json()["username"] == "karan"
    assert client.post("/auth/logout").status_code == 403  # no CSRF header
    assert client.post("/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    client.cookies.set("artha_session", r.cookies["artha_session"])
    assert client.get("/auth/me").status_code == 401  # session deleted server-side


def test_expired_session_rejected(client: TestClient) -> None:
    engine = create_engine(get_settings().db_owner_url.get_secret_value())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO ref.auth_sessions (token_hash, csrf_token, expires_at) VALUES (:t, 'c', :e)"),
            {"t": _token_hash("old-token"), "e": datetime.now(UTC) - timedelta(minutes=1)},
        )
    engine.dispose()
    client.cookies.set("artha_session", "old-token")
    assert client.get("/auth/me").status_code == 401


def test_health_degrades_without_database(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import arthashreni.api.app as appmod

    def broken(*_a: object, **_k: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(appmod, "session_scope", broken)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded" and r.json()["database"] == "unavailable"
