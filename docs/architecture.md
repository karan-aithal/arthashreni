# ArthaShreni — Architecture

Status: Phase 0 draft, 2026-11-11. Binding rules live in `CLAUDE.md`; this document describes how the code will satisfy them.

## 1. Shape of the system

One local machine, one user, one Python process for the API plus one for scheduled jobs, one PostgreSQL database. No cloud services. No outbound traffic except to the public data sources listed in `docs/data-sources/feasibility.md`.

```
                 data/imports/inbox/  (files dropped by user or IMAP job)
                          |
  public sources ---> ingestion (adapters) ---> raw payload store (hash + file)
                          |
                          v
                         dq  (schema, staleness, jumps, conflicts, quarantine)
                          |
                          v
        PostgreSQL:  ref | demo | real   (bitemporal market data, append-only ledger)
                          |
             as_of() reads only
                          |
          +---------------+----------------+
          v               v                v
       ledger         analytics          risk
     (lots, FIFO)   (returns, XIRR,     (signals, levels,
                    allocation, FX)      scenarios, alerts)
          \               |                /
           +-------> policy (budget envelope, IPS, decision engine)
                          |
                  recommendation JSON (versioned schema)
                          |
         +----------------+----------------+
         v                v                v
        api           reports          ai (Phase 8, optional,
     (FastAPI)     (templates)          text only, placeholders)
                                            |
                                    research (Phase 7)
```

Arrows point from producer to consumer. Nothing flows back from `ai` into numbers.

## 2. Packages and boundaries

Single Python distribution `arthashreni`, source in `src/arthashreni/`. Each subpackage below is a module boundary enforced by `import-linter` (see `docs/verify-design.md`).

| Package | Owns | May import | Must not import | Phase |
| --- | --- | --- | --- | --- |
| `core` | `Measured`, `QualityStatus`, money/time helpers, config loading, logging | stdlib, pydantic | everything else in `arthashreni` | 1, 3 |
| `db` | SQLAlchemy models, sessions, schema selection (`demo`/`real`), `as_of` query functions | `core` | `api`, `ai`, `research` | 1 |
| `ledger` | assets, accounts, transactions, corrections, lots, FIFO, reconciliation | `core`, `db` | `ai`, `research`, `ingestion.social`, web frameworks | 2 |
| `ingestion` | adapters (`fetch`/`parse`), importers (CAS, Console, INDmoney, CSV), inbox watcher | `core`, `db`, `dq` | `policy`, `risk`, `ai` | 2, 3 |
| `dq` | E3 checks, quarantine queue, confidence calculation | `core`, `db` | `ai`, `research`, `ingestion.social`, web frameworks | 3 |
| `analytics` | F1 calculations, all pure functions returning `Measured` | `core`, `dq.measured`, `ledger` types | `db`, `ai`, `research`, `ingestion`, `api`, `cli`, sqlalchemy, web frameworks | 4 |
| `portfolio` (module) | loads ledger, prices and FX for one dataset and calls `analytics` (`build_report`); used by `api` and `cli` | `analytics`, `db`, `ledger` | — | 4 |
| `risk` | F2 signals, levels, scenarios, alerts | `core`, `db`, `analytics` | `ai`, `research`, `ingestion.social`, web frameworks | 5 |
| `policy` | budget envelope, reserves, IPS, personal context, decision engine | `core`, `db`, `analytics`, `risk` | `ai`, `research`, `ingestion.social`, web frameworks | 5 |
| `reports` | template-based monthly report (Markdown/PDF) | `core`, `policy`, `risk`, `analytics` | `ai` | 5 |
| `api` | FastAPI app, auth, routers; thin layer over the packages above | all except `ai` internals | broker execution (none exists) | 1+ |
| `jobs` | APScheduler definitions, run history, missed-run recovery | `ingestion`, `dq`, `risk`, `policy`, `reports` | — | 3+ |
| `backtest` | SIP timing study (F4), walk-forward harness | `core`, `analytics`, `db.as_of` | market-data repositories/tables directly | 10 |
| `research` | fundamentals, overlap, IPO tracker, news, claims | `core`, `db.as_of`, `analytics` | market-data repositories/tables directly | 7 |
| `ai` | Ollama client, prompts, numeric-claim and citation verification | `core`, read-only result schemas | `ledger`/`policy` write paths | 8 |

Rules that follow from the table:

- Business logic never imports an adapter. Adapters write observations; everything else reads them through `db`.
- `analytics`, `risk`, `policy` are pure where possible: inputs in, `Measured` out, no I/O. The service layer in `api`/`jobs` loads data and calls them.
- There is no module that can talk to a broker. `import-linter` forbids any module named `*execution*`, `*broker*`, `*order*` (ADR-0011).

## 3. Data separation

Three PostgreSQL schemas (ADR-0004):

- `ref`: shared, non-personal reference data. Asset identifiers (ISIN, AMFI code, ticker), trading calendars metadata, data-source registry, tax rules mirror, job run history.
- `demo`: complete personal + market tables for the DEMO dataset, populated only from committed fixtures.
- `real`: identical table set for the investor's real data. Created empty in Phase 1; written to only after the Phase 4 gate.

A session is bound to exactly one dataset schema via `search_path` at connection checkout. No query joins `demo` and `real`. Every API response carries `dataset: "DEMO" | "REAL"` and the UI must render DEMO visibly.

## 4. Data flow per category

| Flow | Path |
| --- | --- |
| Statement import | inbox file → format detection → parser → `transaction_imports_raw` (unchanged) → normalise + fingerprint → preview queue → user approves → `transactions` → lots rebuilt → reconciliation vs statement holdings |
| Market data | scheduler → adapter `fetch(window)` → raw payload stored (sha256 + file) → schema validation (pandera/pydantic) → `parse` → observations (bitemporal) → E3 checks → `ok` or quarantine |
| Monthly decision | reconciliation clean? → as_of snapshot → analytics → risk → policy hard gates → policy allocation → vetoes → advisory score → cost rule → envelope validator → recommendation JSON → template report → user approval recorded (nothing executed) |

## 5. Runtime

- API: `uvicorn` bound to `127.0.0.1` only. Local single-user auth (ADR-0012).
- Jobs: separate process (`arthashreni jobs run`), APScheduler with a Postgres job store, timezone `Asia/Kolkata`, missed-run recovery on start-up.
- CLI: `arthashreni` (Typer) for imports, reports, backup/restore, gate check.
- Linux (Ubuntu 22.04) is the only target (ADR-0018); Docker Compose is optional convenience only.

## 6. Environment audit (2026-11-11, development machine)

| Item | Found | Note |
| --- | --- | --- |
| OS | Ubuntu 22.04.5 LTS | Only supported OS (ADR-0018) |
| CPU | AMD Ryzen 7 7800X3D, 8 cores / 16 threads, AVX2 + AVX-512 | Good for CPU-only LLM inference later |
| RAM | 30 GiB total | Matches profile (~32 GB) |
| Disk | `/home` 234 GB; 3.3 GB free at audit, **17 GB free at review** (2026-11-11) | Resolved; investor confirms sufficient space |
| Python | system 3.10.12; `python3.12` 3.12.16 present | Use 3.12 via `uv` |
| uv | 0.12.0 | Package manager and lockfile (ADR-0002) |
| PostgreSQL | 14.24 server, cluster `14/main` online on 5432; no role for user `xlr8` yet | Sufficient (NUMERIC, CHECK, triggers, schemas). One-time role setup in Phase 1 |
| pg_dump / psql | 14.24 | Backup tool matches server |
| LibreOffice | 7.3.7.2 (`soffice`) | XIRR oracle, headless |
| make | present | `make verify` wraps `scripts/verify.py` (ADR-0013) |
| Docker | 29.9.2 | Optional |
| Node | 24.20.0 | Not used before Phase 6 |
| Ollama | client 0.24.0, server not running | Not used before Phase 8 |
| Repository | empty, branch `main`, no commits | Phase 0 adds documents only |
