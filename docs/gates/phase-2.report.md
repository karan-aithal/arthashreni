# Phase 2 gate report — Ledger and imports (API/CLI only)

Date: 2026-11-11. Branch `phase-2-ledger-imports`. Agent stops here and waits for `docs/gates/phase-2.approved`.

## 1. Summary
- Append-only ledger in the database (triggers plus read/insert-only privileges): accounts, transactions with corrections and voids, corporate actions, statement holdings, lots with FIFO, reconciliation.
- Pure lot engine (FIFO, split, bonus, merger) with an independent reference model, and a golden DEMO portfolio whose expected values come from a separate script.
- Importers: own CSV templates, Zerodha Console (assumed format), INDmoney (assumed format), CAS via `casparser`; preview, duplicate detection, blocking rules, commit with reconciliation; inbox scan and polling watcher.
- DEMO is isolated by schema and database role; REAL stays locked until the Phase 4 gate.
- No new network access, no frontend, no market-data code.

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Golden portfolio imports and reconciles with zero unexplained differences | pass | `arthashreni demo seed` → 3 files COMMITTED (23 transactions, 3 corporate actions, 7 statement holdings); `reconciliation {'MATCHED': 7, 'UNEXPLAINED': 0, 'ACCEPTED': 0}`. `tests/test_golden.py::test_reconciles_with_zero_unexplained_differences`; `open_differences() == []` |
| Golden figures equal independently derived values | pass | `tests/test_golden.py::test_holdings_lots_and_realised_figures_match_the_independent_derivation`: 5 holdings, 13 lots, 9 consumptions, realised cost 39,347.8000 / proceeds 72,263.4000, remaining cost 22,877.8035, net cash per account. Expected values come from `tests/golden/derive_expected.py` (imports nothing from `arthashreni`); `make verify` re-runs it with `--check` |
| Duplicate re-import is a no-op | pass | second `demo seed` → `ALREADY_IMPORTED` for all three files; row and batch counts unchanged (`test_repeat_import_is_a_no_op`). A renamed copy with different bytes → 23 rows `DUPLICATE`, 0 inserted (`test_same_rows_in_a_renamed_file_are_all_duplicates`); overlapping files add only new rows (`test_overlapping_file_only_adds_new_rows`) |
| CAS units reconcile | pass, with a caveat (V21) | per transaction `units × NAV ≈ amount` (net of stamp duty), closing balance equals casparser's recomputed balance, statement closing units reconcile to the ledger: `tests/test_parsers.py` (mismatch rejected as `UNITS_MISMATCH`, balance mismatch and casparser warnings block), `test_cas_import_end_to_end_with_blocking_on_unit_mismatch`. Tested against casparser's data models, **not real PDFs** |
| Property tests for ledger invariants pass | pass | 8 properties at 1,000 examples each (`make verify` step): holdings equal a running-balance model; `as_of` equals the model at any cut; consumptions equal a naive FIFO queue; cost and proceeds conserved exactly; rebuild deterministic and order-independent; bonus whole units only; SQL-independent effective-view reference; fingerprints unique and prefix-stable |
| `make verify` green | pass | §3 |

## 3. `make verify` result
```
-- ruff check / ruff format / mypy --strict (41 files): ok
-- no float in money packages: ok
-- import contracts: ok            (4 kept, 0 broken; negative test: an `import fastapi` in ledger breaks 2)
-- gate check: ok
-- secret scan: ok
-- tests: ok                       (126 passed)
-- branch coverage >= 95% (ledger): ok     ledger 99% (580 stmts, 1 missed; 142 branches, 1 partial)
-- property tests (1000 examples): ok      8 passed
-- golden values reproducible: ok
-- golden fixtures: ok             6 passed
-- XIRR oracle / mutation testing / tax-rule check: skipped (Phase 4 / no tax rules yet)
-- dependency audit: ok            (pip-audit: no known vulnerabilities)
verify: all steps passed
```
Overall branch coverage 96% (ingestion 91–100% per module). Mutation testing starts at the Phase 4 gate, as planned.

## 4. Changes
```
migrations/versions/0002_ledger.py          src/arthashreni/ledger/{types,models,lots,effective,cash,fingerprint,
src/arthashreni/demo.py                       reconcile,assets,repository,reconciliation}.py
src/arthashreni/ingestion/{common,tables,build,csv_template,console,indmoney,cas,detect,pipeline,inbox,setup_csv}.py
src/arthashreni/cli.py (account, asset, import, ledger, reconcile, demo groups)   src/arthashreni/db/session.py (engine cache)
demo/portfolio/{accounts,assets,corporate_actions,transactions,holdings}.csv      scripts/check_no_float.py
tests/{helpers,test_lots,test_ledger_properties,test_ledger_db,test_parsers,test_pipeline,test_golden,test_cli_phase2}.py
tests/golden/{derive_expected.py,expected.json}   tests/conftest.py (per-module databases, hypothesis profiles)
docs: ADR-0022, data-model, runbook, verify-design, verify-register, ADR-0006 amendment
```
- **Migration 0002:** `ref.assets` and `ref.reject_mutation()`; per dataset (`demo`, `real`) `accounts, import_batches, transaction_imports_raw, transactions, transactions_effective (view), corporate_actions, statement_holdings, lots, lot_consumptions, reconciliations`. Append-only triggers (update/delete/truncate) on raw imports, transactions, corporate actions and statement holdings; app roles get SELECT/INSERT there, plus DELETE on the derived lots tables and limited UPDATE on batch status and reconciliation results. Up/down/up verified.
- **CLI:** `account add|list|load`, `asset add|load`, `import template|file|scan|watch|show|commit|reject`, `ledger holdings|correct|void`, `reconcile run|report|open|accept`, `demo seed`.
- **No new API endpoints and no new environment variables.**
- **Dependencies added:** casparser 1.4.1 (MIT) with casparser-isin (MIT) and pypdfium2 (BSD-3/Apache-2.0); openpyxl (MIT); dev: hypothesis (MPL-2.0), types-openpyxl. No PyMuPDF, no denied packages.

## 5. Run commands (Linux)
```bash
make install && make migrate                # as in docs/runbook.md; migration 0002 is new
uv run arthashreni demo seed                # DEMO portfolio through the real import pipeline
uv run arthashreni ledger holdings
uv run arthashreni reconcile report
make verify
```
Full import and correction commands: `docs/runbook.md` ("Ledger and imports").

## 6. Decisions
- **ADR-0022** records the conventions I chose where C1/C2 were silent: sign of net cash, derived cash, cost basis (fees and stamp duty in, `taxes` stored but not in cost), corrections (`corrected_type`, one per row, void), reserved types, bonus handling, event order, IPO events, FX conversion, duplicate fingerprints, blocking rules, CAS unit tolerance, masked account labels, polling watcher.
- **ADR-0006 amended:** a bonus is a zero-cost lot dated at the ex-date, not a split-like adjustment (the earlier wording would have under-stated gains).
- Assumptions to confirm: bonus entitlement uses holdings at the ex-date (record date unknown); allotment date approximated by the ex-date; one source per account (the same trade imported from two sources is not caught as a duplicate).
- \[VERIFY\]: V14 and V15 stay partly open (formats assumed, parsers warn). New open: V21 (no real PDF tested), V22 (stamp-duty rate not hard-coded, read from the CAS).

## 7. Known limitations
- **No real statement has been parsed.** The Console and INDmoney layouts are assumed; the CAS parser is tested through casparser's models. The first real import must be previewed and read before committing; send a redacted sample so the parsers can be fixed against it.
- Zerodha Coin mutual-fund orders are not covered by the Console parser until a sample shows where they appear (V14); mutual funds currently come in through CAMS/KFintech CAS or the CSV template.
- Depository CAS gives holdings only, so those holdings will show as unexplained until the transactions behind them are imported.
- Tax is not computed; `taxes` is stored, and the cost-basis treatment of taxes is a tax-phase decision.
- The inbox watcher is a polling loop (`import watch`); the scheduled job arrives in Phase 3.
- Splits with fractional results round to 8 decimals; mergers do not model cash in lieu; demergers are not supported.
- Loss of precision at the CSV/XLSX boundary is avoided by reading numbers as text, but spreadsheets that already stored a number as a float may carry representation noise.

## 8. Proposed next phase (Phase 3 — Data quality and core ingestion)
Scope: source registry, adapter contract, AMFI NAV, FX series (FBIL via Frankfurter's FBIL endpoint, SBI TT archive, broker rate), one US price source (Tiingo) or manual price import, SEC EDGAR fundamentals, trading calendars, the `Measured` type, all E3 checks, quarantine queue, bitemporal storage with `as_of` reads, daily ingest job on APScheduler, source health endpoint.
Risks: SBI TT history is a single unofficial archive; the FBIL mirror lags about a week; free-tier quotas; the DEMO dataset must keep using committed fixtures only (no live data), so Phase 3 tests need recorded responses.
