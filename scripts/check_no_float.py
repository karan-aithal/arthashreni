#!/usr/bin/env python3
"""Fail if money code uses float (ADR-0003). Stdlib only.

Scans the packages where numbers are computed: ledger, analytics, policy, risk. Flags `float(...)`,
float literals, `float` annotations and `import math`-free float helpers such as `round(x)` on
floats are not detectable, so the rule is: Decimal in, Decimal out, convert at the boundary.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("ledger", "analytics", "policy", "risk")


def violations(source: str, name: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source, name)):
        where = f"{name}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append(f"{where}: float literal {node.value!r}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
            found.append(f"{where}: float() call")
        elif isinstance(node, ast.Name) and node.id == "float" and isinstance(node.ctx, ast.Load):
            found.append(f"{where}: float used as a name or annotation")
    return found


def main(root: Path = ROOT) -> int:
    bad: list[str] = []
    for pkg in PACKAGES:
        base = root / "src" / "arthashreni" / pkg
        for path in sorted(base.rglob("*.py")) if base.is_dir() else []:
            bad += violations(path.read_text(), str(path.relative_to(root)))
    for line in bad:
        print(f"float: {line}", file=sys.stderr)
    if not bad:
        print("no float in money packages")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
