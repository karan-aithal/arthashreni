#!/usr/bin/env python3
"""Phase-gate check (CLAUDE.md Part G2). Stdlib only; exits 1 on any violation.

Phase N work may exist only if docs/gates/phase-(N-1).approved exists.
Only the investor creates *.approved files.
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = "src/arthashreni"

# Path (relative to repo root) -> phase that introduces it.
PHASE_PATHS: dict[str, int] = {
    f"{PKG}/core": 1,
    f"{PKG}/db": 1,
    f"{PKG}/api": 1,
    f"{PKG}/cli.py": 1,
    f"{PKG}/backup.py": 1,
    "migrations": 1,
    f"{PKG}/ledger": 2,
    f"{PKG}/ingestion": 2,
    f"{PKG}/dq": 3,
    f"{PKG}/jobs": 3,
    f"{PKG}/analytics": 4,
    f"{PKG}/risk": 5,
    f"{PKG}/policy": 5,
    f"{PKG}/reports": 5,
    "apps/web": 6,
    f"{PKG}/research": 7,
    f"{PKG}/ai": 8,
    f"{PKG}/backtest": 10,
}

# Dependencies allowed only once a phase is approved to start (needs phase-(N-1).approved).
PHASE_DEPS: dict[int, set[str]] = {
    6: {"plotly", "matplotlib", "bokeh", "altair", "dash"},  # chart/web libs
    8: {
        "ollama",
        "sentence-transformers",
        "chromadb",
        "faiss-cpu",
        "pgvector",
        "qdrant-client",
        "lancedb",
        "llama-index",
        "langchain",
    },
}

# Never allowed (ADR-0011, ADR-0021, MVP social exclusion).
DENIED_DEPS = {
    "kiteconnect",
    "streamlit",
    "pymupdf",
    "fitz",
    "google-api-python-client",
    "google-auth-oauthlib",
    "praw",
    "tweepy",
    "discord-py",
    "discord.py",
}
FORBIDDEN_NAME = re.compile(r"(^|/)(execution|broker\w*|orders?)(\.py)?$|(^|/)social(\.py)?$")


def approved(phase: int, gates: Path) -> bool:
    return phase < 0 or (gates / f"phase-{phase}.approved").is_file()


def locked_deps(root: Path) -> set[str]:
    lock = root / "uv.lock"
    if not lock.is_file():
        return set()
    data = tomllib.loads(lock.read_text())
    return {p["name"].lower() for p in data.get("package", [])}


def check(root: Path = ROOT) -> list[str]:
    gates = root / "docs" / "gates"
    errors: list[str] = []
    for rel, phase in PHASE_PATHS.items():
        if (root / rel).exists() and not approved(phase - 1, gates):
            errors.append(f"{rel} is Phase {phase} work but phase-{phase - 1}.approved is missing")

    deps = locked_deps(root)
    for phase, names in PHASE_DEPS.items():
        for name in sorted(deps & names):
            if not approved(phase - 1, gates):
                errors.append(f"dependency {name} needs phase-{phase - 1}.approved")
    for name in sorted(deps & DENIED_DEPS):
        errors.append(f"dependency {name} is never allowed (ADR-0011/ADR-0021)")

    src = root / "src"
    if src.is_dir():
        for path in src.rglob("*"):
            rel = path.relative_to(src).as_posix()
            if FORBIDDEN_NAME.search(rel):
                errors.append(f"forbidden module path {rel} (no execution or social code)")

    if (root / "package.json").exists() and not approved(5, gates):
        errors.append("package.json (frontend) needs phase-5.approved")
    return errors


def main() -> int:
    errors = check()
    for e in errors:
        print(f"gate: {e}", file=sys.stderr)
    if not errors:
        print("gate check ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
