# ADR-0018: Linux-only target; what git tracks

Status: Accepted, 2026-11-11 (investor instructions). Part 2 revised at the Phase 0 review the same day: documents are version-controlled.

## Decision
1. **Linux only.** The supported platform is Ubuntu 22.04 on the investor's machine. Windows support is dropped from A2 and from every phase gate ("fresh clone runs on Linux and Windows" becomes "on Linux"). Code may use Linux-native tools (`pg_dump`, `soffice`, `make`, systemd) without Windows fallbacks.
2. **Git tracks** code, `CLAUDE.md`, `docs/` (including `docs/gates/` reports and approval files) and synthetic test fixtures.
   **Git never tracks** secrets (`.env*` except `.env.example`, keys, age identities), anything under `data/` (inbox, raw statements, raw payloads, backups), and statement-type files (`*.pdf`, `*.xlsx`, `*.xls`) outside `tests/fixtures/`. The master build prompt stays local; its Parts A–G are tracked as `CLAUDE.md`.

## Consequences
- A fresh clone has the gate files, so `scripts/check_gates.py` works on a clone.
- Real statements can't be committed by accident; synthetic fixtures must live under `tests/fixtures/`.
