#!/usr/bin/env python3
"""Mutation gate (G1.7): read `mutmut results` and fail if too many mutants in `analytics`, `ledger` or
`policy` survived. A mutant with no covering test counts as surviving. Run `mutmut run` first.

The threshold is 10% (CLAUDE.md G1.7), applied to each package separately.
"""

import subprocess
import sys
from collections import Counter

PACKAGES = ("analytics", "ledger", "policy")
THRESHOLD = 0.10
NOT_KILLED = {"survived", "no tests", "suspicious", "timeout", "segfault", "not checked"}


def main() -> int:
    out = subprocess.run(
        ["mutmut", "results", "--all", "true"], capture_output=True, text=True, check=True
    ).stdout
    counts: dict[str, Counter[str]] = {p: Counter() for p in PACKAGES}
    for line in out.splitlines():
        name, _, status = line.strip().rpartition(": ")
        parts = name.split(".")
        if len(parts) > 2 and parts[0] == "arthashreni" and parts[1] in counts:
            counts[parts[1]][status] += 1
    bad = 0
    for pkg, c in counts.items():
        total = sum(c.values())
        if total == 0:
            print(f"mutation {pkg}: no mutants (package absent or not mutated)")
            continue
        survived = sum(n for s, n in c.items() if s in NOT_KILLED)
        ratio = survived / total
        verdict = "ok" if ratio < THRESHOLD else "FAILED"
        detail = ", ".join(f"{s} {n}" for s, n in sorted(c.items()))
        print(
            f"mutation {pkg}: {survived}/{total} not killed = {ratio:.1%} "
            f"(limit {THRESHOLD:.0%}) {verdict}  [{detail}]"
        )
        bad += ratio >= THRESHOLD
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
