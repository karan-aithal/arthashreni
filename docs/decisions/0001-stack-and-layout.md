# ADR-0001: Stack and repository layout

Status: Accepted, 2026-11-11

## Context
Part H fixes the stack: Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, PostgreSQL, APScheduler; Next.js later. The machine has Python 3.12.16 and PostgreSQL 14.24.

## Decision
- One Python distribution `arthashreni`, `src/` layout: `src/arthashreni/{core,db,ledger,ingestion,dq,analytics,risk,policy,reports,api,jobs}`. `research`, `ai`, `backtest` are created only in their phases.
- CLI with Typer (`arthashreni ...`).
- PostgreSQL 14 is the minimum supported version (no features newer than 14 used). Native cluster on Linux (ADR-0018); Docker Compose optional.
- Repository top level: `src/`, `tests/` (`unit/`, `property/`, `golden/`, `fixtures/`), `migrations/`, `config/`, `scripts/`, `docs/`, `data/` (git-ignored except `.gitkeep` and templates). `apps/web/` appears only in Phase 6.

## Consequences
Single package keeps imports simple and lets `import-linter` police boundaries. Postgres 14 floor matches the installed server, so no upgrade is needed.
