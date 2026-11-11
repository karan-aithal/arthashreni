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

## Ledger and imports (Phase 2)

```bash
make migrate                                  # applies 0002_ledger (ledger, imports, lots, reconciliation)
uv run arthashreni demo seed                  # loads demo/portfolio/ into the DEMO dataset (safe to re-run)
uv run arthashreni ledger holdings            # units per account and asset
uv run arthashreni reconcile report           # every statement holding vs the ledger
```

Setting up your own data (DEMO or, after the Phase 4 gate, REAL):

```bash
uv run arthashreni account add KITE --platform ZERODHA_KITE --kind BROKER
uv run arthashreni asset add EQUITY_IN "Some Co" INR IN --isin INE000A01010 --ticker SOMECO --mic XNSE
uv run arthashreni account load accounts.csv ; uv run arthashreni asset load assets.csv   # or in bulk
uv run arthashreni import template transactions        # CSV header + example (also: corporate-actions, holdings)
```

Importing a statement (nothing reaches the ledger until you commit):

```bash
uv run arthashreni import file PATH [--account LABEL] [--as-of YYYY-MM-DD]   # preview; PDFs ask for the password
uv run arthashreni import show BATCH                  # rejected / ambiguous / duplicate rows with reasons
uv run arthashreni import commit BATCH [--accept-skipped]
uv run arthashreni import reject BATCH                # discard; the same file can be imported again
uv run arthashreni import scan | watch                # preview whatever is in data/imports/inbox/ (once / forever)
```

Inbox layout: files that name their own account (CSV templates, CAS) go in `data/imports/inbox/`; Zerodha and INDmoney
exports do not, so put them in a sub-folder named after the account label (`inbox/KITE/tradebook.csv`). PDFs are listed
but imported with `import file` because they need a password.

Fixing mistakes never edits history:

```bash
uv run arthashreni ledger correct TXN_ID --reason "keyed wrong units" --set quantity=8 --set net_cash_inr=-80 --set net_cash_orig=-80
uv run arthashreni ledger void TXN_ID --reason "duplicate"
uv run arthashreni reconcile open                       # differences that block the monthly review
uv run arthashreni reconcile accept ID --reason "..."   # only with a recorded reason
```

## Market data and data quality (Phase 3)

Nothing is fetched until you consent per source. In `.env`:

```
ARTHA_CONSENTED_SOURCES=amfi_nav,sbi_tt_archive,fbil_ref,tiingo_eod,sec_companyfacts   # only the ones you accept
ARTHA_TIINGO_API_KEY=...            # personal key, never committed
ARTHA_SEC_USER_AGENT="Your Name your.email@example.com"   # sent to the SEC only
```

```bash
uv run alembic upgrade head                      # migration 0003
uv run arthashreni demo market                   # DEMO: synthetic prices, NAV, FX, fundamentals (no network)
uv run arthashreni market ingest [--source ID] [--force]   # fetch from consented sources
uv run arthashreni market sources                # health: state, freshness, quota, pending items
uv run arthashreni market price AMFI:900001 --date 2026-11-10    # value + quality status
uv run arthashreni market fx FBIL_REF            # rate type is required (SBI_TT_BUY, FBIL_REF, BROKER_ACTUAL)
uv run arthashreni market import-prices FILE     # manual CSV: asset_ref,valid_at,close,currency,price_type
uv run arthashreni market import-fx FILE         # manual FBIL CSV: rate_type,valid_at,rate
uv run arthashreni asset cik TICKER:AAPL 320193  # needed for SEC fundamentals
```

Review queue (a pending item blocks the monthly review, like an unexplained ledger difference):

```bash
uv run arthashreni quarantine list [--all]
uv run arthashreni quarantine accept ID --note "checked on the exchange site"      # keeps the value as a new ok row
uv run arthashreni quarantine correct ID --value 101.5 --note "decimal slip"       # new row with your value
uv run arthashreni quarantine reject ID --note "bad tick"
```

Scheduler (Asia/Kolkata, daily at `ARTHA_INGEST_TIME`, default 20:00):

```bash
uv run arthashreni scheduler catch-up            # run slots missed while the machine was off
uv run arthashreni scheduler run                 # catch up, then run until stopped (systemd user service, ADR-0019)
```

API (login required): `GET /sources/health`, `GET /quarantine`.

Capturing real fixtures (after consent, key and User-Agent are set): run `market ingest --source tiingo_eod --force`, then the same for `sec_companyfacts`; the raw bodies are in `data/raw/<source>/`. A mismatch shows as `SCHEMA_DRIFT` in `quarantine list`. Procedure and the "never commit the key" rule: ADR-0023.

A source that is down shows `failing`; stale data is labelled `stale` with both dates; the rest keeps working. AMFI history
is not available from its daily file: use the CAS or a manual CSV. The FBIL mirror lags about a week, so expect `stale` there.

## Datasets

`ARTHA_DATASET=demo` (default) or `real`. `real` is refused until `docs/gates/phase-4.approved` exists.
