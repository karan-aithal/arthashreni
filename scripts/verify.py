#!/usr/bin/env python3
"""`make verify` (ADR-0013, docs/verify-design.md). Exit non-zero on the first failure.

Steps whose targets do not exist yet (later phases) are reported as skipped.
--fast runs the pre-commit subset. --keep-going runs every step.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "arthashreni"

# Packages that need >= 95% branch coverage once they exist (G1.8).
COVERAGE_PACKAGES = ["ledger", "dq", "analytics", "policy", "risk"]

# Outside PYTHONPATH (e.g. ROS) must not leak plugins into the project environment.
ENV = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


Step = tuple[str, list[str] | None, dict[str, str]]


def steps(fast: bool) -> list[Step]:
    cov_pkgs = [p for p in COVERAGE_PACKAGES if (PKG / p).is_dir()]
    pytest = [
        "pytest",
        "-q",
        "--allow-hosts=127.0.0.1,::1",
        "--cov=arthashreni",
        "--cov-branch",
        "--cov-report=term-missing:skip-covered",
    ]
    s: list[Step] = [
        ("ruff check", ["ruff", "check", "."], {}),
        ("ruff format", ["ruff", "format", "--check", "."], {}),
        ("mypy --strict", ["mypy", "--strict", "src", "scripts"], {}),
        ("no float in money packages", [sys.executable, "scripts/check_no_float.py"], {}),
        ("import contracts", ["lint-imports"], {}),
        ("gate check", [sys.executable, "scripts/check_gates.py"], {}),
        ("secret scan", [sys.executable, "scripts/secret_scan.py"], {}),
        ("tests", pytest, {}),
    ]
    if fast:
        return s
    s += [
        (
            "branch coverage >= 95% (ledger, dq, analytics, policy, risk)",
            ["coverage", "report", "--fail-under=95", *[f"--include=src/arthashreni/{p}/*" for p in cov_pkgs]]
            if cov_pkgs
            else None,
            {},
        ),
        # Same property tests as above, re-run with the `ci` profile (1,000 examples each).
        (
            "property tests (1000 examples)",
            ["pytest", "-q", "-m", "property", "--no-cov", "--allow-hosts=127.0.0.1,::1"],
            {"HYPOTHESIS_PROFILE": "ci"},
        ),
        ("golden values reproducible", [sys.executable, "tests/golden/derive_expected.py", "--check"], {}),
        (
            "golden fixtures",
            ["pytest", "-q", "--no-cov", "--allow-hosts=127.0.0.1,::1", "tests/test_golden.py"],
            {},
        ),
        ("XIRR oracle", None, {}),  # Phase 4
        ("mutation testing", None, {}),  # Phase 4
        (
            "tax-rule check",
            [sys.executable, "scripts/check_tax_rules.py"]
            if (ROOT / "config" / "tax_rules.yaml").exists()
            else None,
            {},
        ),
        ("dependency audit", ["pip-audit", "--progress-spinner=off", "--skip-editable"], {}),
    ]
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--keep-going", action="store_true")
    args = ap.parse_args()
    failed: list[str] = []
    for name, cmd, extra_env in steps(args.fast):
        if cmd is None:
            print(f"-- {name}: skipped (no targets yet)")
            continue
        t0 = time.monotonic()
        rc = subprocess.run(cmd, cwd=ROOT, env={**ENV, **extra_env}, check=False).returncode
        status = "ok" if rc == 0 else f"FAILED (exit {rc})"
        print(f"-- {name}: {status} [{time.monotonic() - t0:.1f}s]")
        if rc != 0:
            failed.append(name)
            if not args.keep_going:
                break
    print("verify: " + ("FAILED: " + ", ".join(failed) if failed else "all steps passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
