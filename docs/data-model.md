# ArthaShreni — Data model

Status: Phase 0 design, 2026-11-11. Implemented by Alembic migrations from Phase 1 onward; columns may be refined per phase, invariants may not.

## Conventions

| Concern | Rule |
| --- | --- |
| Money | `NUMERIC(20,4)`, Python `Decimal`. Never float (ADR-0003) |
| Quantity / units | `NUMERIC(24,8)`. MF units are compared to CAS at 3 decimals |
| Prices, NAV, FX | `NUMERIC(24,8)` |
| Percent / ratios | `NUMERIC(12,8)` as fractions (0.25 = 25%) |
| Timestamps | `TIMESTAMPTZ` in UTC plus `source_tz TEXT` (IANA name) where the source gave local time |
| Business dates | `DATE` (trade date, NAV date, valid date) |
| IDs | `BIGINT GENERATED ALWAYS AS IDENTITY`; external identifiers in separate columns |
| Enums | PostgreSQL `TEXT` + `CHECK (col IN (...))`, mirrored by Python `StrEnum` |
| Append-only tables | trigger rejects `UPDATE`/`DELETE`; app role has no `UPDATE`/`DELETE` privilege (ADR-0005) |
| Quality | every observation row has `quality_status` in `ok, stale, missing, conflicting, estimated, quarantined` |

Schemas: `ref` (shared, non-personal), `demo` and `real` (identical personal + market tables). See ADR-0004. Tables below are in each of `demo`/`real` unless marked `ref`.

## 1. Reference (`ref`)

**assets** — one row per instrument.
`id, asset_class (EQUITY_IN, EQUITY_US, MF, ETF_IN, ETF_US, GOLD_ETF, SILVER_ETF, SGB, DIGITAL_GOLD, CASH), name, isin NULL, amfi_code NULL, ticker NULL, exchange_mic NULL (XNYS, XNAS, XBOM, XNSE), currency, country, mf_plan (DIRECT, REGULAR) NULL, mf_option (GROWTH, IDCW) NULL, fund_house NULL, sector NULL, market_cap_bucket NULL, is_sme BOOLEAN DEFAULT false, regulated BOOLEAN, maturity_date DATE NULL, coupon_rate NUMERIC(12,8) NULL, coupon_frequency_months INT NULL, created_at`.
SME assets (`is_sme = true`) are outside the investment universe: policy never allocates to them and IPO eligibility is always false (ADR-0021). SGB rows use the maturity/coupon fields (ADR-0020).
Unique: `isin`, `amfi_code`, `(ticker, exchange_mic)` where not null.

**asset_identifier_map** — AMFI ↔ ISIN ↔ ticker history.
`asset_id, id_type, id_value, valid_from, valid_to, source_id`.

**data_source_registry** — D2.
`id TEXT PK, name, provider (who serves the data, e.g. `frankfurter`), underlying_series (whose data it is, e.g. `FBIL`), data_category, adapter, auth_type, free_tier, rate_limit, historical_coverage, expected_delay INTERVAL, terms_url, storage_permitted, redistribution_permitted, priority INT, enabled BOOLEAN, last_success_at, last_failure_at, notes`.

**tax_rules** — mirror of `config/tax_rules.yaml`, reloaded at start-up.
`id TEXT PK, jurisdiction, asset_class, holding_period, rate_or_method JSONB, effective_from DATE, effective_to DATE NULL, source_url, verified_on DATE, review_by DATE`.

**local_user** (Phase 1) — `id SMALLINT PK CHECK (id = 1), username, password_hash (Argon2id), created_at`. Exactly one user. App roles: SELECT only; created by `arthashreni user init` as owner.

**auth_sessions** (Phase 1) — `token_hash TEXT PK (SHA-256 of cookie token), csrf_token, created_at, expires_at`. App roles: SELECT, INSERT, DELETE.

**job_runs** — `id, job_name, scheduled_for, started_at, finished_at, status (ok, failed, skipped), attempt, detail JSONB`. Unique `(job_name, scheduled_for)` for idempotency.

## 2. Accounts and ledger

**accounts** — `id, platform (INDMONEY, ZERODHA_COIN, ZERODHA_KITE, CAMS, KFINTECH, OTHER), account_kind (BROKER, MF_FOLIO, DEMAT, BANK, RESERVE), label, folio_or_ref_masked, currency, opened_on, closed_on NULL`.
Real identifiers are stored masked except the last 4 characters (security model).

**import_batches** — `id, source_kind (CAS_CAMS, CAS_KFIN, CAS_NSDL, CAS_CDSL, ZERODHA_CONSOLE, INDMONEY, CSV_TEMPLATE, MANUAL), file_name, file_sha256 UNIQUE, received_at, status (PREVIEW, COMMITTED, REJECTED), approved_at NULL, statement_period_start, statement_period_end`.

**transaction_imports_raw** (append-only) — `id, batch_id, row_no, raw JSONB, fingerprint TEXT, parse_status (NEW, DUPLICATE, AMBIGUOUS, REJECTED), reject_reason NULL`.

**transactions** (append-only) — C1.
`id, asset_id NULL (NULL only for pure cash types), account_id, txn_type, trade_date, settlement_date NULL, price_date NULL, quantity NUMERIC NULL, price NUMERIC NULL, currency, fx_rate NULL, fx_rate_type (SBI_TT_BUY, FBIL_REF, BROKER_ACTUAL) NULL, fees, taxes, stamp_duty, net_cash_inr, net_cash_orig, source_document, import_batch_id NULL, raw_record_id NULL, verification_status (UNVERIFIED, STATEMENT_MATCHED, MANUAL_CONFIRMED), supersedes_id NULL REFERENCES transactions(id), correction_reason NULL, fingerprint TEXT UNIQUE, recorded_at, source_tz`.
`txn_type` ∈ C1 list plus `INTEREST` (SGB coupons, ADR-0020). `CHECK (txn_type <> 'CORRECTION' OR (supersedes_id IS NOT NULL AND correction_reason IS NOT NULL))`. `CHECK (fx_rate IS NULL OR fx_rate > 0)`. `CHECK (fx_rate IS NULL OR fx_rate_type IS NOT NULL)`.

**transactions_effective** (view) — latest non-superseded version of each logical transaction. A CORRECTION row carries the corrected values in full; a correction with `quantity = 0` and `net_cash = 0` acts as a void. Reports read this view; audit reads the table.

**corporate_actions** (append-only) — `id, asset_id, action_type (SPLIT, BONUS, MERGER, DEMERGER, NAME_CHANGE), ex_date, ratio_from, ratio_to, successor_asset_id NULL, source_document, recorded_at`.

**lots** — derived, rebuildable from the ledger at any time (never the source of truth).
`id, transaction_id, asset_id, account_id, acquired_on, original_qty, cost_inr, cost_orig, fx_rate_type, adjusted_by_ca_id NULL`.

**lot_consumptions** — derived. `id, lot_id, disposal_transaction_id, qty, cost_inr_consumed, proceeds_inr, holding_days`. FIFO by `(acquired_on, transaction_id)` (ADR-0006).

**reconciliations** — `id, batch_id, run_at, asset_id, account_id, ledger_units, statement_units, diff, status (MATCHED, UNEXPLAINED, ACCEPTED), accepted_reason NULL`. Any `UNEXPLAINED` row blocks the monthly review.

## 3. Market data (bitemporal, append-only)

All carry `valid_at DATE` (the fact's date), `recorded_at TIMESTAMPTZ`, `source_id`, `raw_payload_id`, `quality_status`, `quality_notes TEXT[]`. Restatements are new rows. Reads only via `as_of(valid_at, known_at)` (ADR-0008).

- **prices** — `asset_id, valid_at, close, currency, price_type (CLOSE, NAV, STATEMENT), import_batch_id NULL`. For `STATEMENT` prices, `valid_at` is the price date printed on the statement, `recorded_at` is when the statement was imported, and `import_batch_id` names the statement. Price age = valuation date − `valid_at`, computed, never stored. When no newer price exists the last valid one is used with status `stale` and its age shown; no other instrument's price is ever substituted (ADR-0015).
- **fx_rates** — `pair (USDINR), rate_type (SBI_TT_BUY, FBIL_REF, BROKER_ACTUAL), underlying_series (FBIL, SBI, BROKER), provider (frankfurter, sbi_fx_ratekeeper, indmoney_statement, manual), valid_at, rate, retrieved_at`. `rate_type` names the series, never the provider: FBIL's 94.8606 for 2026-11-03 fetched from Frankfurter is stored as `rate_type=FBIL_REF, underlying_series=FBIL, provider=frankfurter, valid_at=2026-11-03`, with `retrieved_at` the fetch time. A successful fetch says nothing about freshness; staleness is judged from `valid_at` against the FBIL publication calendar (E3).
- **fundamentals** — `asset_id, concept (XBRL tag), period_end, fiscal_period, unit, value, filed_at, accession_no`.
- **macro_series** — `series_id, valid_at, value, unit`.
- **fund_holdings** — `fund_asset_id, disclosure_date, holding_isin NULL, holding_name, weight, market_value`.
- **raw_payloads** (append-only) — `id, source_id, fetched_at, window_start, window_end, sha256, file_path, http_status, schema_ok BOOLEAN`.
- **dq_quarantine** — `id, table_name, row_ref, check_name, detail JSONB, created_at, resolution (PENDING, ACCEPTED, CORRECTED, REJECTED), resolved_at, resolution_note`.
- **source_conflicts** — `id, asset_id, valid_at, observation_ids BIGINT[], chosen_id, rule, diff_pct`.

## 4. Budget, reserves, IPS (Phase 5)

**budget_settings** (append-only) — `id, monthly_budget_inr, effective_from, recorded_at`.

**sip_mandates** — `id, asset_id, account_id, amount_inr, day_of_month, platform, start_date, end_date NULL, status (ACTIVE, PAUSED, ENDED)`. History via new rows on change.

**budget_periods** — B1, one row per month.
`period (DATE, first of month) PK, opening_budget_inr, committed_sip_inr, new_allocations_inr, reserve_transfers_inr, unallocated_inr`.
`CHECK (committed_sip_inr + new_allocations_inr + reserve_transfers_inr + unallocated_inr = opening_budget_inr)` and every component `>= 0`.

**envelope_lines** — `id, period, kind (SIP, NEW_ALLOCATION, RESERVE_TRANSFER, UNALLOCATED), amount_inr CHECK (amount_inr >= 0), asset_id NULL, reserve_id NULL`.
Deferred constraint trigger: per period, sum of lines by kind equals the matching `budget_periods` column.

**recommendation_lines** — `id, recommendation_id, envelope_line_id UNIQUE REFERENCES envelope_lines, action, amount_inr, ...`. `UNIQUE(envelope_line_id)` makes double allocation impossible.

**reserves** — `id, name, kind (CASH_RESERVE, IPO_SINKING_FUND), target_inr NULL`.
**reserve_ledger** (append-only) — `id, reserve_id, entry_date, kind (TRANSFER_IN, USE, DIVIDEND_IN, SALE_PROCEEDS_IN, IPO_BLOCK, IPO_RELEASE), amount_inr, approved_line_id NULL`. Balance (sum) must never go negative: enforced by a constraint trigger that rejects the insert.

**ips_versions** — `id, version INT, status (DRAFT, APPROVED, SUPERSEDED), body JSONB (validated by Pydantic schema), approved_at NULL, approved_note NULL`. Unique partial index: at most one `APPROVED`.

**personal_context** — `field, value JSONB, certainty (KNOWN, ASSUMED, UNKNOWN), updated_at`. History via new rows.

**ipo_applications** — `id, ipo_ref, rhp_document, min_lot_value_inr, applied_on, status`; money movements live in `transactions` (IPO_APPLICATION, IPO_BLOCK, IPO_ALLOTMENT, IPO_REFUND) and `reserve_ledger`.

## 5. Risk and decisions (Phase 5, 9)

- **risk_signals** — `id, as_of, signal_id, scope (MARKET, PORTFOLIO, ASSET), asset_id NULL, value, status, threshold_ref`.
- **risk_levels** — `as_of, level (NORMAL, CAUTION, HIGH_RISK, SEVERE_STRESS), drivers JSONB`.
- **alerts** — `id, dedupe_key UNIQUE, first_raised_at, last_seen_at, body JSONB (F2 fields), state (OPEN, ACKNOWLEDGED, CLOSED)`.
- **recommendations** — `id, period, schema_version, payload JSONB, confidence (HIGH, MEDIUM, LOW), created_at, human_approval_required BOOLEAN CHECK (human_approval_required)`.
- **decisions** (append-only) — `id, recommendation_id, line_id NULL, decision (APPROVE, REJECT, MODIFY, DEFER), reason TEXT NOT NULL, decided_at`. Recording a decision executes nothing.

## 6. Later phases (named only)

`research_claims`, `news_events`, `ipo_research`, `llm_calls`, `embeddings` (Phase 7–8; vector store only via ADR).

## 7. Invariant-to-mechanism map

| Invariant | Mechanism |
| --- | --- |
| A3.1 budget | `budget_periods` CHECK; `envelope_lines` sum trigger; `UNIQUE(envelope_line_id)`; Pydantic validator; hypothesis tests |
| A3.2 no execution | no broker module; import-linter forbidden-module contract; `decisions` table only records |
| A3.4 ledger is truth | append-only trigger + revoked privileges; CORRECTION rows; lots are derived |
| A3.7 quality travels | `quality_status` on every observation; `Measured` on every result |
| A3.8 demo isolation | separate `demo` schema; session bound to one schema; `dataset` field in every response |
| Concurrent double allocation | `SELECT ... FOR UPDATE` on the `budget_periods` row inside the allocation transaction, plus `UNIQUE(envelope_line_id)` (ADR-0009) |
| E4 no look-ahead | bitemporal rows; `as_of` functions; import-linter on `backtest`/`research` |
