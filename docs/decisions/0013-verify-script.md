# ADR-0013: `make verify` implemented as a Python script

Status: Accepted, 2026-11-11

## Context
Thirteen steps with skip rules and `--keep-going` are easier to read and test in Python than in Make syntax.

## Decision
`scripts/verify.py` runs every step in `docs/verify-design.md` and exits non-zero on the first failure (with `--keep-going` to run all). `Makefile` target `verify` calls `uv run python scripts/verify.py`. Pre-commit runs the fast subset; the full run is required for gates.

## Consequences
`make verify` stays the one command; the logic lives in tested Python.
