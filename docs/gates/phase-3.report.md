# Phase 3 gate report — Data quality and core ingestion

Date: 2026-11-11. Branch `phase-3-data-quality`. Agent stops here and waits for `docs/gates/phase-3.approved`.

## 1. Summary
- Source registry (23 rows = `feasibility.md`), adapter contract (`fetch`/`parse`, rate limit, retries with backoff, timeouts) and adapters for AMFI NAV, SBI TT buying, FBIL (Frankfurter FBIL provider), Tiingo EOD, SEC companyfacts and manual price/FX CSV.
- `Measured` type and quality ordering, source-specific calendars (NYSE, BSE for listed instruments only, and AMFI / FBIL / SBI derived from each source's own history), all E3 checks, quarantine queue with accept / correct / reject, bitemporal storage with `as_of` reads (migration 0003).
- Daily ingest job on APScheduler (Asia/Kolkata, persistent store, catch-up of missed slots), `GET /sources/health`, CLI `market`, `quarantine`, `scheduler`.
- Consent per source before any request (`ARTHA_CONSENTED_SOURCES`); DEMO market data is synthetic and served through the real adapters with no network.
- **No live fetch was made** (no consent set, no Tiingo key, no SEC User-Agent). AMFI, SBI and Frankfurter shapes were checked against real responses by hand; Tiingo and SEC bodies are hand-built from documentation (V23) and stay so until consent and credentials exist.

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Simulated provider outage caught and quarantined | pass | `tests/test_phase3_gate.py::test_gate_provider_outage`: Tiingo answers 503 → 4 calls recorded (1 + 3 retries with backoff), run `FAILED`, one `provider_outage` item, source health `failing`, stored data untouched, other source (FBIL) still `OK`; recovery auto-clears the item. Also `test_market_db.py::test_provider_outage_is_recorded_alerted_once_and_cleared_on_recovery` (second failure queues nothing new) |
| Schema change caught and quarantined | pass | `test_gate_schema_change`: AMFI header renamed, FBIL envelope changed, SBI columns changed → each `SCHEMA_DRIFT`, 2 raw payloads stored with `schema_ok = false`, `schema_drift` items, **no rows stored** (`fx_rates` count unchanged). Adapter-level cases in `tests/test_adapters.py` (AMFI header/row/NAV/date, SBI columns/date/rate, FBIL shape ×4, Tiingo ×3, SEC ×3, manual CSV ×4) |
| Price spike caught and quarantined | pass | `test_gate_price_spike`: DTECH close doubled on 10 Nov → stored `quarantined`, item `unexplained_jump`, `price_as_of` ignores it (returns the last good close, labelled `stale`), `blocks_monthly_review = true`; rejecting it with a reason via CLI unblocks. Split on the same day (`splitFactor 2`) is *not* quarantined. Pure cases in `tests/test_dq.py`. Real-data case: the SBI archive's `TT BUY 0.00` rows are quarantined as `impossible_state` (`test_market_db.py::test_impossible_fx_rate_is_quarantined…`) |
| Stale data labelled | pass | `as_of` returns `Measured` with status `stale` and a note naming both dates (`latest 2026-11-09, expected 2026-11-10 (1 days behind)`); a holiday is not stale (Thanksgiving: `test_stale_is_labelled_but_a_holiday_is_not_stale`); source-specific calendars (`test_amfi_gap_is_a_holiday_even_when_the_bse_was_open`, `test_fbil_and_sbi_use_their_own_publication_days`, `test_derived_calendars_follow_the_source_not_the_bse`); SBI TT never plain `ok`; `market sources` and `/sources/health` show freshness per source |
| import-linter contracts pass | pass | 5 kept, 0 broken (new: `dq` pure; `db` forbids `ingestion`) |

### E3 checks
| Check | Where | Result |
| --- | --- | --- |
| Staleness (source-specific calendars) | `dq/checks.staleness`, `db/as_of`, `dq/health` | `stale` / `missing` / `ok` |
| Unexplained jump | `dq/checks.jump`, runner | quarantine; corporate action (ledger or `splitFactor`) explains |
| Source disagreement | runner cross-checks (FBIL vs SBI 3%; fetched vs statement price 2%) | both kept, `source_conflicts`, reads `conflicting` |
| Ledger vs statement | `dq/health` (Phase 2 reconciliation) | open differences block the monthly review |
| Impossible state | `dq/checks.impossible_value` + DB `CHECK` | quarantine. Negative holdings and units after full exit are the ledger's own rules (Phase 2) |
| Schema drift | adapters + runner | run quarantined, one alert |

## 3. `make verify` result
```
-- ruff check / ruff format / mypy --strict (64 files): ok
-- no float in money packages (now incl. dq): ok
-- import contracts: ok            (5 kept, 0 broken)
-- gate check: ok
-- secret scan: ok
-- tests: ok                       (234 passed)
-- branch coverage >= 95% (ledger, dq): ok     dq 97% (311 stmts, 5 missed; 80 branches, 4 partial), ledger 99%
-- property tests (1000 examples): ok          9 passed
-- golden values reproducible: ok
-- golden fixtures: ok             6 passed
-- XIRR oracle / mutation testing / tax-rule check: skipped (Phase 4 / no tax rules yet)
-- dependency audit: ok            (pip-audit: no known vulnerabilities)
verify: all steps passed
```
Migration 0003: up / down 0002 / up / down base / up verified on a scratch database. Overall branch coverage 97%. Mutation testing starts at the Phase 4 gate, as planned.

## 4. Changes
```
migrations/versions/0003_market_data.py
src/arthashreni/dq/{measured,calendars,params,checks,store,health}.py      src/arthashreni/db/as_of.py
src/arthashreni/ingestion/sources/{contract,http,registry,amfi,fx,tiingo,sec,manual,runner,ingest}.py
src/arthashreni/{jobs,demo_market}.py   src/arthashreni/api/sources.py   cli.py (market, quarantine, scheduler, demo market, asset cik)
demo/market/* + scripts/make_demo_market.py   tests/{test_dq,test_adapters,test_market_db,test_phase3_gate}.py   tests/fixtures/recorded/*
docs: ADR-0023, data-model, runbook, verify-design, verify-register (V23–V27), feasibility findings, ADR-0019 note
```
- **Migration 0003:** `ref.data_source_registry`, `ref.source_calls`, `ref.job_runs`, `ref.apscheduler_jobs`, `ref.assets.cik`; per dataset `raw_payloads, prices, fx_rates, fundamentals` (append-only triggers, as in 0002) and `dq_quarantine, source_conflicts` (app role may update the three resolution columns only). A non-positive price/rate is accepted only on a `quarantined` row.
- **API:** `GET /sources/health`, `GET /quarantine` (login required, read-only). **New env vars:** `ARTHA_CONSENTED_SOURCES`, `ARTHA_TIINGO_API_KEY`, `ARTHA_SEC_USER_AGENT`, `ARTHA_INGEST_TIME`, `ARTHA_RAW_DIR`, all optional.
- **Dependencies added:** exchange-calendars 4.13.2 (Apache-2.0, pulls pandas/numpy), APScheduler 3.11 (MIT), httpx (BSD, moved from dev). No `pandera` (ADR-0023), no denied packages.

## 5. Run commands (Linux)
```bash
make migrate                                   # migration 0003
uv run arthashreni demo market                 # DEMO market data, no network
uv run arthashreni market sources
uv run arthashreni market price AMFI:900001 --date 2026-11-10
uv run arthashreni quarantine list
make verify
```
Live fetching needs your consent and keys first: `docs/runbook.md` ("Market data and data quality").

## 6. Decisions
### Investor decisions after review (2026-11-11), all applied
| # | Decision | Applied as |
| --- | --- | --- |
| 1 | Tiingo/SEC fixtures stay documentation-derived; capture real responses only after consent and credentials exist, then validate schemas and update the recorded fixtures | Unchanged fixtures, labelled as hand-built in `tests/test_adapters.py`. Capture procedure written down (ADR-0023 "Fixtures and live capture", runbook). V23 = open (deferred). No live call made |
| 2 | DQ thresholds are provisional, never described as historically validated; recalibrate in an upcoming phase | Wording fixed in ADR-0023 and V27 (open, provisional); report says "provisional" throughout. Values unchanged (`dq/params.py`) |
| 3 | Source-specific calendars; drop the assumption that BSE holidays apply to AMFI and FBIL | Implemented: `XNYS` (NYSE), `XBOM` (only BSE/NSE-listed prices), derived `AMFI`, `FBIL`, `SBI` calendars from each source's own observed days, `WEEKDAY` fallback with an explicit "calendar not yet known" note. Per-source health no longer borrows the BSE for fund NAVs or FX. V26 resolved by design |
| 4 | Every zero SBI TT buying rate stays quarantined | Unchanged and covered by a new test (four zero rows → four quarantined, none readable) |
| 5 | SBI is not promoted to verified without independent corroboration | Rule written into ADR-0023: only a second independent archive of SBI's TT rate can promote it; agreement with FBIL does not (different quantity). New test: SBI stays `estimated` when equal to FBIL |
| 6 | ADR-0023 keeps the scheduling optimization and the Pydantic validation decisions, with rationale and test evidence | Both are headed "(decision, kept)" in ADR-0023 with rationale and a named list of tests |
| 7 | Keep live provider capture deferred until consent and credentials exist | Yes. Nothing contacted a live source in this phase |
| 8 | Password rotation: reminder at the end, no repeated asking | See the last line of this report and of the hand-over message |

### Conventions chosen (ADR-0023)
Consent model, run outcomes, quarantine resolution, jump parameters, disagreement thresholds, staleness reference day, source-specific calendars, fundamentals visibility, AMFI storage, scheduling catch-up, Pydantic validation. ADR-0023 amends ADR-0019 in one place: missed daily slots are all recorded but only the newest fetches (adapters fetch look-back windows). Pydantic plus header checks replace `pandera` (D3 names it).

## 7. Known limitations
- **No live source was contacted.** Tiingo's token header and the SEC body are from documentation (V23). A mismatch appears as `SCHEMA_DRIFT` on the first real run, not as bad data.
- **Thresholds are provisional and not historically validated** (V27): k = 6, floors 5% / 1% / 1%, 2% and 3% disagreement. Recalibration is deferred to an upcoming phase, once real history exists.
- **Derived calendars learn from history** (V26): AMFI, FBIL and SBI publication days come from our own observations, so a genuine holiday right after the newest observation reads `stale` for that one day, and with no history weekdays are assumed (the note says so). `exchange_calendars` covers XBOM only to 2026-12-31; later dates fall back to weekdays.
- **AMFI gives one date per fetch**; NAVs for days the machine was off cannot be fetched later — use CAS or a manual CSV. The FBIL mirror lagged about a week on 2026-11-11, so expect `stale` there.
- **SBI TT buying has no second archive** (V2): always `estimated`, and promoted only by a second independent archive. 47 archive rows hold `0.00` and all are quarantined.
- **Indian ETF/stock prices** come only from statements or manual CSV (NSE/BSE automation not permitted, V4): they read `stale` between statements by design.
- Tiingo fails the whole run if one ticker fails (marked `ponytail:` in code); the scheduler process is started by hand or a systemd user unit (not shipped); desktop notifications are not built (alerts are quarantine items plus the health endpoint).
- Not built (later phases): FRED/RBI macro, fund holdings, Alpha Vantage, `backtest`/`research` import contracts (the packages do not exist yet).

## 8. Proposed next phase (Phase 4 — Calculation engine)
Scope: everything in F1 as pure `Measured` functions: invested capital, market value (INR and original currency, FX with an explicit rate type), realised/unrealised P&L, XIRR (own, checked against `pyxirr` and LibreOffice) and TWR, FX attribution, allocation and concentration, volatility, drawdown, correlation, benchmarks; API and CLI reports. Gate: oracle comparison, golden fixtures, mutation and coverage thresholds, FX attribution reconciles. After that gate the investor may import real data.
Risks: no real data yet to tune the Phase 3 thresholds; benchmark and look-through data are free-source limited; mutation testing adds runtime to `make verify`.

**Reminder (investor action, from Phase 1):** rotate the `artha_owner` password that a malformed command echoed in part: delete the `ARTHA_DB_*` and `ARTHA_DATASET` lines from `.env`, then run `bash scripts/db_setup.sh`.
