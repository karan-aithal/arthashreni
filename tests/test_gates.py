import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_gates", ROOT / "scripts" / "check_gates.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cg = _load()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "gates").mkdir(parents=True)
    (tmp_path / "src" / "arthashreni" / "core").mkdir(parents=True)
    return tmp_path


def approve(repo: Path, n: int) -> None:
    (repo / "docs" / "gates" / f"phase-{n}.approved").write_text("ok")


def test_real_repository_passes() -> None:
    assert cg.check(ROOT) == []


def test_phase1_work_needs_phase0_approval(repo: Path) -> None:
    assert any("phase-0.approved" in e for e in cg.check(repo))
    approve(repo, 0)
    assert cg.check(repo) == []


def test_later_phase_package_blocked(repo: Path) -> None:
    approve(repo, 0)
    (repo / "src" / "arthashreni" / "ledger").mkdir()
    assert any("ledger" in e for e in cg.check(repo))
    approve(repo, 1)
    assert cg.check(repo) == []


def test_frontend_blocked_before_phase6(repo: Path) -> None:
    approve(repo, 0)
    (repo / "apps" / "web").mkdir(parents=True)
    assert any("apps/web" in e for e in cg.check(repo))


def test_dependencies(repo: Path) -> None:
    approve(repo, 0)
    (repo / "uv.lock").write_text('[[package]]\nname = "ollama"\n\n[[package]]\nname = "kiteconnect"\n')
    errors = cg.check(repo)
    assert any("ollama" in e and "phase-7" in e for e in errors)
    assert any("kiteconnect" in e and "never" in e for e in errors)


@pytest.mark.parametrize("name", ["broker.py", "execution", "orders.py", "social"])
def test_forbidden_module_names(repo: Path, name: str) -> None:
    approve(repo, 0)
    (repo / "src" / "arthashreni" / name).touch()
    assert any("forbidden module" in e for e in cg.check(repo))
