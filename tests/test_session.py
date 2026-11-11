import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from arthashreni.db import session as s


def test_demo_session_search_path() -> None:
    with s.session_scope("demo") as db:
        assert db.scalar(text("SHOW search_path")) == "demo, ref"
        assert db.scalar(text("SELECT current_user")) == "artha_demo"


def test_demo_role_cannot_touch_real_schema() -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), s.session_scope("demo") as db:
        db.execute(text("CREATE TABLE real.x (a int)"))


def test_demo_role_cannot_create_users() -> None:
    with pytest.raises(ProgrammingError, match="permission denied"), s.session_scope("demo") as db:
        db.execute(text("INSERT INTO ref.local_user VALUES (1, 'u', 'h')"))


def test_real_dataset_locked_until_phase4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s, "gate_approved", lambda n: False)
    with pytest.raises(s.RealDataLocked):
        s.get_engine.__wrapped__("real")  # bypass the cache; don't orphan pooled connections


def test_unknown_dataset_rejected() -> None:
    with pytest.raises(ValueError, match="unknown dataset"):
        s.get_engine.__wrapped__("other")
