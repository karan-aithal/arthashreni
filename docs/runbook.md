# Runbook (Linux, Ubuntu 22.04)

All commands run from the repository root. `make` clears any outside `PYTHONPATH`
(this machine has ROS Humble on it, whose pytest plugin would otherwise load).

## First-time setup

```bash
git clone <repo> arthashreni && cd arthashreni
bash scripts/db_setup.sh        # once per machine; asks for sudo; writes .env (mode 600)
make install                    # uv sync --frozen + git pre-commit hook
make migrate                    # alembic upgrade head (as artha_owner)
make user                       # create the single local login (password >= 12 chars)
```

Moving to a new clone on the same machine: copy `.env` instead of re-running `db_setup.sh`.

## Run

```bash
make run                        # API on http://127.0.0.1:8000  (docs at /docs)
curl -s http://127.0.0.1:8000/health
```

Login: `POST /auth/login` with `{"username": ..., "password": ...}` sets an HttpOnly cookie and
returns a `csrf_token`; send it as `X-CSRF-Token` on every POST/PUT/PATCH/DELETE.

## Verify

```bash
make verify                     # full acceptance check
make verify-fast                # what the pre-commit hook runs
```

Tests create and drop their own database `arthashreni_test`; they never touch `arthashreni`.

## Backups (ADR-0014)

```bash
uv run arthashreni backup keygen --out ~/arthashreni-backup-identity.txt
#   -> prints the public key; put it in .env as ARTHA_BACKUP_RECIPIENT=age1...
#   -> MOVE the identity file off this machine (USB / password manager). Without it no backup can be read.
make backup                     # data/backups/arthashreni-<UTC>.tar.age (mode 600)
make restore-test               # no key needed: dump -> scratch DB -> compare row counts -> drop
uv run arthashreni backup restore data/backups/<file>.tar.age \
    --identity /path/to/identity.txt [--target-db NAME] [--data-out DIR] [--no-keep]
```

`restore` never writes to the main database. To replace the main database after a disaster, restore
into a new name, check it, then (as the postgres superuser) rename databases.

## Datasets

`ARTHA_DATASET=demo` (default) or `real`. `real` is refused until `docs/gates/phase-4.approved` exists.
