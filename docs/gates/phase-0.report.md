# Phase 0 gate report — Architecture and feasibility

Date: 2026-11-11. Agent stops here and waits for `docs/gates/phase-0.approved`.

## Review corrections (2026-11-11)
- `.gitignore` fixed: `CLAUDE.md` and `docs/` are tracked; secrets, `data/` (personal data, raw statements, backups) and statement-type files outside `tests/fixtures/` stay excluded (ADR-0018 revised).
- ADR inventory reconciled: 21 ADRs, matching index; report counts corrected.
- Disk space resolved; restore demonstration scheduled for the Phase 1 gate (see §7).
- No invariant or settled decision changed.

## 1. Summary
- Saved Parts A–G of the master prompt as `CLAUDE.md`.
- Audited the development machine (`docs/architecture.md` §6). Disk space was low at audit time; resolved at review (17 GB free).
- Wrote architecture and module boundaries, full data model, 21 ADRs, security and privacy model, `make verify` design, MVP-vs-later list.
- Checked terms for every Phase 0–4 data source and recorded them in `docs/data-sources/feasibility.md` and `docs/verify-register.md`.
- No application code written.

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Environment and repository audit | pass | `docs/architecture.md` §6 (from `python3.12 --version`, `pg_lsclusters`, `lscpu`, `free -g`, `df -h`, etc.) |
| Architecture and module boundaries (`ledger`, `ingestion`, `dq`, `analytics`, `risk`, `policy`, `research`, `ai`, `api`, `jobs`) | pass | `docs/architecture.md` §2 |
| Full data model | pass | `docs/data-model.md` |
| ADRs for each major choice | pass | `docs/decisions/0001`–`0021`; 21 files, 21 rows in `docs/decisions/README.md`, every `ADR-NNNN` reference in `CLAUDE.md` and `docs/` resolves to an existing file |
| `feasibility.md` with verified terms per source | pass, with open items | `docs/data-sources/feasibility.md`; 6 open items listed at its end |
| Security and privacy model | pass | `docs/security-privacy.md` |
| `make verify` design | pass | `docs/verify-design.md` |
| MVP vs later list | pass | `docs/mvp-scope.md` |
| Every \[VERIFY\] item touched has a source and date | pass | `docs/verify-register.md`: each touched item has a source and 2026-11-11; items with only partial or secondary evidence are marked `open` and are not relied on by any design decision |
| No application code beyond scaffolding | pass | `git status` shows only `.md` files, `.gitignore`, `data/.gitkeep` |

## 3. `make verify` result
Not applicable: designed in this phase, implemented in Phase 1. No code exists to verify.

## 4. Changes
```
CLAUDE.md
README.md
.gitignore
data/.gitkeep
docs/architecture.md
docs/data-model.md
docs/mvp-scope.md
docs/security-privacy.md
docs/verify-design.md
docs/verify-register.md
docs/data-sources/feasibility.md
docs/decisions/README.md, 0001–0017
docs/gates/phase-0.report.md
```
No migrations, endpoints, config or environment variables yet. Planned env vars (Phase 1+): `DATABASE_URL`, `ARTHA_DATASET`, `TIINGO_API_KEY`, `ALPHAVANTAGE_API_KEY`, `FRED_API_KEY`, `SEC_USER_AGENT`, `BACKUP_RECIPIENT`.

## 5. Run commands
None yet. Phase 1 will deliver install/migrate/seed/run/test commands for Linux (ADR-0018).

## 6. Decisions
- ADRs 0001–0021 (index in `docs/decisions/README.md`). All are reversible defaults; none changes a Part A invariant.
- Notable choices:
  - Investor clarifications 2026-11-11: allocation locks the month row (ADR-0009); scheduler persists and catches up missed runs (ADR-0019); FX rows store provider and underlying series separately; statement prices carry their date, import time and age; SGB existing-holdings only (ADR-0020); cron, Streamlit, PyMuPDF, Gmail API, Kite Connect and paid data excluded (ADR-0021).
  - DEMO/REAL separated by PostgreSQL schema **and** DB role (ADR-0004).
  - Append-only enforced by trigger and revoked privileges (ADR-0005).
  - No automated NSE/BSE price download; Indian ETF prices come from statements or manual entry (ADR-0015). This weakens daily risk monitoring for Indian ETFs.
  - Tiingo is the primary US price source; Alpha Vantage's free tier (25/day) is only a fallback.
  - Backups encrypted to an age public key; the private key stays off the machine (ADR-0014).
  - Leftover-cash rule uses the B2 default and is labelled as an assumption (ADR-0017).
- \[VERIFY\] resolved: V1, V3, V4, V5, V6, V8, V9, V10, V11, V12, V13; V20 closed (Linux only). Partly resolved: V2 (second archive), V7 (FBIL terms), V14 (Coin export). Open: V15 (INDmoney format). Not yet touched: V16–V19 (later phases).

## 7. Known limitations
- Disk space: resolved. `df -h /home` at review (2026-11-11): 234G size, 206G used, 17G free (93%). Investor confirms enough space for Postgres data, raw payloads and backups.
- Backup restore not demonstrated in Phase 0: no database or backup code exists yet, and the current Linux user has no PostgreSQL role (`FATAL: role "xlr8" does not exist`). Phase 1 gate demonstrates it after the one-time setup `sudo -u postgres createuser --createdb --createrole xlr8`.
- Linux only (ADR-0018).
- FBIL rates come from the Frankfurter mirror, which lags about a week; FBIL's own terms are still unread.
- Only one SBI TT archive found; those values are stored as `estimated` until a second source exists.
- INDmoney statement format and Zerodha Coin MF export location need a sample file (redacted is fine) in Phase 2.
- AMFI terms forbid storing a "significant portion" of the site. Design stores only held/watched schemes, but the clause is broad.

## 8. Proposed next phase (Phase 1 — Backend foundation)
Scope: `pyproject.toml` + `uv.lock`; `src/arthashreni/{core,db,api}`; Postgres roles and `ref`/`demo`/`real` schemas; Alembic baseline; `.env.example`; structured logging with redaction; `/health`; local auth; encrypted backup + restore scripts; `scripts/verify.py` + `Makefile` + pre-commit; `scripts/check_gates.py`.
Risks: creating the Postgres role needs one superuser step by the investor (`sudo -u postgres createuser --createdb --createrole xlr8`); after that, the backup/restore demonstration is part of the Phase 1 gate.

## Questions for the investor
None open. Disk space resolved; Linux only; documents tracked in git.
