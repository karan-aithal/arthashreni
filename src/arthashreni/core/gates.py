"""Read-only view of investor gate approvals (docs/gates). The code never writes them."""

from pathlib import Path

from arthashreni.core.config import PROJECT_ROOT

GATES_DIR = PROJECT_ROOT / "docs" / "gates"


def gate_approved(phase: int, gates_dir: Path = GATES_DIR) -> bool:
    return (gates_dir / f"phase-{phase}.approved").is_file()
