# Phase 1 gate report — Backend foundation

Date: 2026-11-11. Branch `phase-1-backend-foundation`. Agent stops here and waits for `docs/gates/phase-1.approved`.

## 1. Summary
- Python 3.12 project (uv, locked), FastAPI app with `/health` and local single-user auth (Argon2id, HttpOnly + SameSite=Strict cookie, CSRF header token).
- PostgreSQL: dedicated roles `artha_owner` / `artha_demo` / `artha_real` and database `arthashreni` (`scripts/db_setup.sh`); Alembic baseline creates `ref`, `demo`, `real` schemas with role grants (ADR-0004).
- Config from `.env`, JSON logging with redaction of PAN, emails, long numbers, passwords and DB URLs.
- age-encrypted backup and restore with row-count verification, plus a keyless restore test (ADR-0014).
- `make verify` (`scripts/verify.py`), `scripts/check_gates.py`, secret scan, git pre-commit hook. No frontend.

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Fresh clone to running API with documented commands (Linux) | pass | Cloned branch into a scratch folder, copied `.env`, ran `make install`, `make migrate`, `make run` (port 8765). `curl /health` → `{"status":"ok","dataset":"DEMO","database":"ok","version":"0.1.0"}`; `/auth/me` without login → `401`; `ss -ltn` → `LISTEN 127.0.0.1:8765` only. `make verify` in the clone → `verify: all steps passed` |
| Backup restores into a scratch database | pass | `arthashreni backup keygen` (test key) → `backup create` → file starts `age-encryption.org/v1`, mode `600` → `backup restore ... --no-keep` → `restored into arthashreni_restore_test: 3 tables match, 1 data files`. Keyless `make restore-test` → `restore test ok: 3 tables match`. Tests `test_backup.py`: round trip with 25-row probe table and a data file, wrong key fails, main DB refused |
| `make verify` green | pass | see §3 |
| No frontend project | pass | no `apps/web/`, no `package.json`; `check_gates.py` enforces it |

## 3. `make verify` result
```
-- ruff check: ok
-- ruff format: ok
-- mypy --strict: ok            (16 source files: src + scripts)
-- import contracts: ok         (2 kept, 0 broken)
-- gate check: ok
-- secret scan: ok
-- tests: ok                    (29 passed)
-- branch coverage >= 95% (ledger, dq, analytics, policy, risk): skipped (no targets yet)
-- property tests / golden fixtures / XIRR oracle / mutation testing / tax-rule check: skipped (no targets yet)
-- dependency audit: ok         (pip-audit: no known vulnerabilities)
verify: all steps passed
```
Coverage (informational; the 95% rule applies to packages from Phase 2 on): total 89% branch. `backup.py` 93%, `core/logging.py` 92%, `cli.py` 49% (interactive commands tested manually above). Mutation score: not applicable yet.

## 4. Changes
```
.githooks/pre-commit        Makefile            alembic.ini          pyproject.toml  uv.lock  .python-version
.env.example                scripts/db_setup.sh scripts/check_gates.py scripts/verify.py scripts/secret_scan.py
migrations/env.py  migrations/script.py.mako  migrations/versions/0001_baseline.py
src/arthashreni/{__init__,cli,backup}.py
src/arthashreni/core/{config,gates,logging}.py
src/arthashreni/db/{session,models}.py
src/arthashreni/api/{app,auth}.py
tests/{conftest,test_api,test_backup,test_cli,test_gates,test_logging,test_session}.py
docs/runbook.md (new), docs/data-model.md (auth tables), README.md
```
- **Migration 0001:** schemas `ref`, `demo`, `real`; `ref.local_user` (single row, `CHECK id = 1`), `ref.auth_sessions`; grants: app roles `USAGE` on `ref` + own dataset schema only, `SELECT` on `local_user`, `SELECT/INSERT/DELETE` on `auth_sessions`. Alembic version table in `ref`.
- **Endpoints:** `GET /health`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`.
- **CLI:** `arthashreni serve | user init | backup keygen | backup create | backup restore | backup restore-test`.
- **Environment variables** (`ARTHA_` prefix): `DB_OWNER_URL`, `DB_DEMO_URL`, `DB_REAL_URL`, `DATASET`, `API_HOST`, `API_PORT`, `ALLOW_NON_LOCAL_BIND`, `SESSION_HOURS`, `DATA_DIR`, `BACKUP_DIR`, `BACKUP_RECIPIENT`, `RESTORE_TEST_DB`, `LOG_LEVEL`; test-only `MIGRATE_URL`.
- **Dependencies:** fastapi, uvicorn, sqlalchemy 2.1, psycopg 3 (LGPL-3.0, used as a library), alembic, pydantic, pydantic-settings, argon2-cffi, typer, pyrage (MIT, checked on GitHub 2026-11-11). Dev: ruff, mypy, pytest, pytest-cov, pytest-socket, import-linter, httpx, detect-secrets (Apache-2.0), pip-audit. No denied or phase-gated packages.

## 5. Run commands (Linux)
See `docs/runbook.md`. Short form:
```bash
bash scripts/db_setup.sh     # once per machine (sudo); writes .env
make install                 # uv sync --frozen + pre-commit hook
make migrate                 # alembic upgrade head
make user                    # create local login
make run                     # http://127.0.0.1:8000
make verify
uv run arthashreni backup keygen --out ~/arthashreni-backup-identity.txt   # then set ARTHA_BACKUP_RECIPIENT
make backup && make restore-test
```
Seeding DEMO data: nothing to seed yet (no ledger tables until Phase 2).

## 6. Decisions
- No new ADRs. Implementation follows ADR-0004 (roles + schemas), ADR-0005 (owner runs migrations), ADR-0012 (auth), ADR-0013 (verify script), ADR-0014 (age backups), ADR-0018 (Linux).
- Assumptions/defaults:
  - Outside `PYTHONPATH` is cleared by `make` and `verify.py`, because ROS Humble on this machine injects a pytest plugin that breaks the project environment.
  - Tests run against their own database `arthashreni_test`, created and dropped per run; sockets are disabled except `127.0.0.1`.
  - `restore` refuses the main database; replacing it after a disaster is a documented manual rename.
  - Six detect-secrets findings were placeholders and test strings; each is marked with an inline `pragma: allowlist nextline secret`.
  - `pip-audit` sends package names and versions (no personal data) to the PyPI vulnerability service.
- \[VERIFY\]: V20 closed (Linux only; pyrage MIT confirmed). No other items touched.

## 7. Known limitations
- No production backup key yet: run `backup keygen`, store the identity off this machine, set `ARTHA_BACKUP_RECIPIENT`. The daily schedule and retention pruning come with the scheduler in Phase 3 (ADR-0019).
- The local login user isn't created yet: run `make user`.
- The backup archive is built in memory (fine for this data size; marked in code).
- No login rate limit (local single user, Argon2 cost only). The session cookie has no `Secure` flag because the API is plain HTTP on localhost.
- Starlette deprecation warning for `httpx` in the test client; harmless for now.
- `cli.py` coverage is 49% (interactive prompts).

## 8. Proposed next phase (Phase 2 — Ledger and imports)
Scope: asset registry and identifiers, accounts, append-only ledger with corrections and `transactions_effective`, lots and FIFO with corporate actions, CSV templates, CAS (casparser) and Zerodha Console parsers, INDmoney parser against a sample format, inbox watcher, preview, fingerprint duplicate detection, reconciliation report, DEMO golden portfolio, property tests for ledger invariants.
Risks: INDmoney statement format and Coin MF export location still unconfirmed (need redacted samples); casparser needs a synthetic CAS PDF fixture, since no real statement may be committed.
