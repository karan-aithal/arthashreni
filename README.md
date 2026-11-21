# ArthaShreni

Structured Wealth. Intelligent Decisions.

Personal investment intelligence platform for one Indian investor: ledger, data quality, calculations, risk and policy engine, and a read-only web UI, built in gated phases. Decision support only; it never trades, never stores broker credentials and never sends your data anywhere without logged consent. Not investment advice.

**Status:** Phases 0 to 5 approved, plus the Phase 5 follow-ups (ADR-0029). Phase 6 (read-only UI) is built and waiting for `docs/gates/phase-6.approved`. Everything runs on synthetic DEMO data until you import real statements (allowed after the Phase 4 gate).

| Document | What it covers |
| --- | --- |
| `CLAUDE.md` | The constitution (Parts A to G): invariants and gate rules |
| `docs/architecture.md` | Packages, boundaries, data flow |
| `docs/data-model.md` | Tables and constraints |
| `docs/decisions/` | ADRs (0001 to 0029) |
| `docs/runbook.md` | Detailed operating commands |
| `docs/gates/` | Phase reports, verify logs and approval files |
| `docs/verify-register.md` | Open `[VERIFY]` items (tax, regulatory, source terms) |
| `docs/security-privacy.md` | Security and privacy model |

## What it does

- **Ledger:** append-only transactions with corrections, FIFO lots, corporate actions, statement imports (preview, then approve) and reconciliation against statements.
- **Data quality:** every value carries a status (`ok`, `stale`, `missing`, `conflicting`, `estimated`, `quarantined`) and an as-of date. Bad data goes to a quarantine queue.
- **Calculations:** valuation, P&L, XIRR, TWR, FX attribution, allocation, concentration, volatility, drawdown, benchmarks. All in tested Python.
- **Budget and policy:** one monthly budget (initially ₹10,000) across all asset classes, enforced in the database. SIPs are pre-committed, leftover cash goes to a reserve, and no personalised allocation exists without an approved IPS.
- **Risk:** four risk levels, stress scenarios (not predictions) and de-duplicated alerts. A market fall never triggers a sale by itself.
- **Monthly review:** a recommendation plus a template report, with no LLM. Every recommendation needs your approval, and nothing is executed.
- **Web UI (read-only):** Overview, Portfolio, Transactions & Imports, Data Quality & Sources, Risk Monitor, Monthly Report.

Not built yet: research and IPO module (Phase 7), local LLM (Phase 8), approval workflow in the UI (Phase 9), SIP research (Phase 10).

## Requirements

- Linux (Ubuntu 22.04 target; Windows is not supported, ADR-0018)
- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- PostgreSQL (this machine uses the 18 cluster on port 5433, ADR-0027)
- Node 24 and npm (web UI only)
- `sudo` once, for database setup

## First-time setup

```bash
git clone <repo> arthashreni && cd arthashreni
bash scripts/db_setup.sh        # once per machine; asks for sudo; writes .env (mode 600)
make install                    # uv sync --frozen + git pre-commit hook
make migrate                    # alembic upgrade head (re-run after pulling: migration 0005 adds asset theme and fund category)
make user                       # create the single local login (password of 12+ characters)
```

Load the DEMO data so there is something to look at:

```bash
uv run arthashreni demo seed      # ledger, accounts, statements
uv run arthashreni demo market    # prices, NAVs, FX (recorded fixtures, no network)
uv run arthashreni demo policy    # DEMO budget, SIP, reserves, approved DEMO IPS
```

If the machine has ROS or another global `PYTHONPATH`, use `make` or `env -u PYTHONPATH ...`; a stray pytest plugin otherwise breaks the tests.

## Start the app

Two processes: the API and the web UI.

```bash
# terminal 1: API on http://127.0.0.1:8000 (docs at /docs)
make run

# terminal 2: web UI on http://127.0.0.1:3100
cd apps/web
npm ci                                                  # first time only
ARTHA_API_URL=http://127.0.0.1:8000 npm run build
ARTHA_API_URL=http://127.0.0.1:8000 npm run start
```

Open http://127.0.0.1:3100 and sign in with the login from `make user`.

- `ARTHA_API_URL` is baked in at build time (the browser only talks to the Next server, which proxies `/api/*` to FastAPI). If you change it, rebuild.
- If port 8000 is taken, set `ARTHA_API_PORT=8010` for `make run` and use the same port in `ARTHA_API_URL`.
- For development: `npm run dev` in `apps/web` (same port, 3100).
- Check the API without the UI: `curl -s http://127.0.0.1:8000/health`.
- Both servers bind to localhost only. The API refuses other addresses unless `ARTHA_ALLOW_NON_LOCAL_BIND=true`.

## Configuration (`.env`)

`scripts/db_setup.sh` writes `.env`; `.env.example` shows every key. Never commit `.env`.

| Variable | Meaning |
| --- | --- |
| `ARTHA_DB_OWNER_URL`, `ARTHA_DB_DEMO_URL`, `ARTHA_DB_REAL_URL` | Database roles: owner (migrations), demo and real datasets |
| `ARTHA_DATASET` | `demo` (default) or `real`. `real` is refused until `docs/gates/phase-4.approved` exists |
| `ARTHA_API_PORT` | API port (default 8000) |
| `ARTHA_CONSENTED_SOURCES` | Comma-separated external sources you consent to contact. Empty means nothing is fetched |
| `ARTHA_TIINGO_API_KEY`, `ARTHA_SEC_USER_AGENT` | Optional keys and contact string for market-data adapters |
| `ARTHA_INGEST_TIME` | Daily ingest time, Asia/Kolkata (default 20:00) |
| `ARTHA_NOTIFY` | `desktop` (default) raises a local `notify-send` notification for new risk alerts; `off` silences it. Test with `arthashreni scheduler notify-test` |
| `ARTHA_BACKUP_RECIPIENT` | `age` public key for encrypted backups |
| `ARTHA_API_URL` | Web UI only: API address the proxy targets (set at `npm run build`) |

Your own settings live in files: `config/costs.toml` (platform fees; DEMO uses `demo/costs.toml`) and `config/risk_thresholds.toml` (provisional, not backtested).

## Using it with the CLI

Everything that changes data goes through the CLI. The UI only reads. `uv run arthashreni --help` lists all groups; add `--help` after any group.

| Group | Purpose |
| --- | --- |
| `account`, `asset` | Broker accounts and the asset registry. `asset set REF --category DEBT` (mutual funds: EQUITY, HYBRID, DEBT) and `--theme NAME` feed scenarios and the IPS theme limit; unknown counts as equity |
| `import` | Preview a statement file, then commit or reject. Nothing commits without your approval |
| `ledger`, `reconcile` | Holdings, corrections, ledger vs statement differences |
| `market` | Data sources, ingest, prices, FX |
| `quarantine` | Review queue: accept, correct or reject with a reason |
| `portfolio` | Valuation and return reports |
| `budget`, `sip`, `reserve`, `ipo`, `context`, `ips` | Budget, SIP mandates, reserves, IPO sinking fund, personal context, Investment Policy Statement |
| `risk`, `review` | Risk run, monthly review, record a decision (never executes anything) |
| `scheduler` | Daily ingest, risk and monthly jobs (the monthly review starts on the first BSE trading day) |
| `backup`, `user`, `demo` | Encrypted backups, local login, DEMO data |

Typical monthly flow, once real data and an approved IPS exist:

```bash
uv run arthashreni import --help                      # drop a statement in data/imports/inbox/, preview, commit
uv run arthashreni reconcile report                   # must be clean before the review
uv run arthashreni risk run --save
uv run arthashreni review run                         # dry run: prints the report, stores nothing
uv run arthashreni review run --commit
uv run arthashreni review decide <id> --decision APPROVE --reason "..."
```

Details, including budget, SIP, IPS and cost setup, are in `docs/runbook.md`.

## Moving to real data

1. Write your own IPS (`uv run arthashreni ips template` is an example only) and approve it with `ips approve`.
2. Put your platforms' real fees in `config/costs.toml`.
3. Set `ARTHA_DATASET=real`, then import your CAS and broker statements through `import` and read the first preview yourself.
4. Resolve every reconciliation difference. Unexplained differences block the monthly review.

Without an approved IPS, real months produce only a "what is missing" report. DEMO and REAL data live in separate database schemas and never mix.

## Tests and checks

```bash
make verify         # full acceptance check: lint, mypy --strict, import contracts, tests,
                    # property tests, golden fixtures, XIRR oracle, mutation tests, audits, web tests
make verify-fast    # what the pre-commit hook runs
cd apps/web && npm test      # web contract, accessibility and read-only tests
```

After an API change that alters a response, regenerate the UI fixtures:

```bash
UPDATE_UI_FIXTURES=1 uv run pytest tests/test_phase6_contract.py --no-cov
```

Tests create and drop their own `arthashreni_test` databases and never touch your data.

## Backups

```bash
uv run arthashreni backup keygen --out ~/arthashreni-backup-identity.txt   # put the public key in .env; keep the identity file OFF this machine
make backup           # encrypted archive in data/backups/
make restore-test     # dump, restore into a scratch database, compare, drop
```

## Repository layout

```
src/arthashreni/    core, db, ledger, ingestion, dq, analytics, risk, policy, reports, api, jobs, cli
apps/web/           Next.js read-only UI (TypeScript, SCSS modules) and its test fixtures
migrations/         Alembic migrations
config/, demo/      Risk thresholds; DEMO portfolio, market data and costs
data/               Local only and git-ignored: inbox, raw payloads, backups
scripts/            verify, gate check, DB setup, oracle and secret scans
tests/              Unit, property, golden and contract tests
docs/               Architecture, ADRs, runbook, phase reports and gates
```

## Phase gates

Work proceeds one phase at a time. Each phase ends with `docs/gates/phase-N.report.md`; only you create `docs/gates/phase-N.approved`, and `scripts/check_gates.py` blocks later-phase code without it.

<img width="813" height="977" alt="Screenshot from 2026-11-11 00-40-47" src="https://github.com/user-attachments/assets/c3f543c2-89bc-440d-abac-203b14857bad" />
