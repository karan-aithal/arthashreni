import pytest
from typer.testing import CliRunner

from arthashreni.cli import app
from arthashreni.core.config import get_settings


def test_serve_refuses_non_local_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTHA_API_HOST", "0.0.0.0")  # noqa: S104 - testing the refusal
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["serve"])
    get_settings.cache_clear()
    assert result.exit_code != 0
    assert "refusing to bind" in result.output
