# ADR-0023: Data-quality and ingestion conventions (Phase 3)

Status: Accepted, 2026-11-11; revised 2026-11-11 after investor review (source-specific calendars, SBI rule, provisional thresholds, deferred capture). Fills gaps in Parts D and E; amends ADR-0019 in one place (catch-up).

## Decisions

**Consent (invariant 10).** A request to a source reveals which instruments the investor follows (Tiingo tickers, AMFI codes). Nothing is fetched unless the source id is listed in `ARTHA_CONSENTED_SOURCES`. Unconsented sources are `SKIPPED`, never failed. Only the declared SEC User-Agent (`ARTHA_SEC_USER_AGENT`) carries a contact detail, and only to the SEC. The Tiingo key travels in a header, never in a URL. `ref.source_calls` records status and a short reason per call, never a URL, symbol or key. httpx's own INFO log (full URLs) is silenced.

**Payload validation: Pydantic, not `pandera` (decision, kept).** D3 names `pandera` for tables. Every payload in Phase 3 is a small list, CSV or JSON document with a fixed header; Pydantic models plus exact header/column checks give the same guarantee (whole-run quarantine on any mismatch) with one dependency fewer (pandera pulls in pandas-heavy validation machinery we do not otherwise need for a 10-row payload). Add `pandera` if wide tables arrive (fund holdings, Phase 7).
- *Evidence:* `tests/test_adapters.py` drives every adapter with malformed input: AMFI (header, scheme code, NAV, date), SBI (columns, date, rate), FBIL (4 shape changes incl. wrong currency pair), Tiingo (3), SEC (3), manual CSV (columns, date, number, type); each must raise `SchemaDrift`. `tests/test_phase3_gate.py::test_gate_schema_change` shows the consequence end to end: run `SCHEMA_DRIFT`, raw payload kept with `schema_ok = false`, nothing stored.

**Run outcomes.**
| Event | Result |
| --- | --- |
| Outage (network error, timeout, 5xx/429 after 3 retries with backoff 1, 2, 4 s) | run `FAILED`, registry `last_failure_at`, one `provider_outage` quarantine item (de-duplicated while pending), auto-cleared (`AUTO_CLEARED`) by the next successful run |
| Schema change (header, shape, number or date no longer parses) | whole run `SCHEMA_DRIFT`; raw payload stored with `schema_ok = false`; one `schema_drift` item; **no observation stored** |
| Impossible value (price, NAV, FX <= 0) | row stored with `quality_status = quarantined`, item `impossible_state` |
| Unexplained jump | row stored as `quarantined`, item `unexplained_jump` |
| Disagreeing sources | both rows kept; `source_conflicts` row; reads label `conflicting` |

The database allows a non-positive value only on a `quarantined` row, so bad data can be reviewed but never read as good.

**Quarantine resolution.** `ACCEPTED` and `CORRECTED` insert a **new** `ok` row (copy, or copy with the corrected value) whose note names the item and the reason; `REJECTED` only closes the item. The quarantined row is never touched. A reason is mandatory and is logged. Pending items block the monthly review together with unexplained ledger differences.

**Jump check (hypotheses, `dq/params.py`).** Flag a move larger than `max(6 x rolling volatility, floor)`. Volatility is the sample standard deviation of up to 60 simple returns; at least 10 returns are required, otherwise the check says "skipped" in the note and the row is `ok`. Floors: prices 5%, NAV 1%, FX 1% (a liquid fund's volatility is near zero and would flag noise). A corporate action within 2 days (ledger `corporate_actions`, or Tiingo `splitFactor != 1`) explains a move. **These values are provisional. They are not historically validated** (there is no real history yet, and one investor's data may never support validation); do not describe them otherwise. Recalibration is deferred to an upcoming phase, once real history has been imported (the Phase 4 gate is the earliest that allows it); until then every threshold lives in `dq/params.py` and the register item V27 stays open.

**Disagreement thresholds.** Fetched price vs the price on a statement trade of the same day: 2%. FBIL vs SBI TT buying: 3%, because SBI's TT buying rate sits about 1.3% under the reference by design (94.8606 vs ~93.6, Nov 2026). The row from the reference series is the chosen one.

**SBI TT stays `estimated` (rule).** One unofficial archive, no second archive (V2): reads return at best `estimated` with the note "single unofficial archive". It is **not promoted to `ok`/verified without independent corroboration**, meaning a second independent archive of SBI's TT buying rate. Agreement with FBIL does not count: FBIL is a different quantity (reference rate, not SBI's TT buying rate). Every `TT BUY 0.00` row (47 in the archive on 2026-11-11, days SBI gave no rate) is quarantined as an impossible value, never read, never "repaired".
- *Evidence:* `test_market_db.py::test_sbi_stays_estimated_even_when_it_agrees_with_fbil`, `test_every_zero_sbi_rate_is_quarantined_not_only_the_first`, `test_impossible_fx_rate_is_quarantined_even_though_the_archive_publishes_it`.

**Staleness and source-specific calendars.** The expected newest date is the last publication day of *the source's own calendar* on or before the reference day, minus the source's publication delay (`expected_delay_days`, shrinking to 0 for dates older than that delay). The reference day is `min(valid_at, known_at)`: the future cannot be stale. No calendar is borrowed from another source:

| Source / asset | Calendar | Basis |
| --- | --- | --- |
| US stock prices | `XNYS` | `exchange_calendars` |
| Exchange-traded Indian ETF/stock prices | `XBOM` | `exchange_calendars`; used **only** for BSE/NSE-listed instruments |
| AMFI NAV (mutual funds) | `AMFI` | derived: days on which more than half of the held schemes have a NAV |
| FBIL reference rate | `FBIL` | derived from FBIL_REF history |
| SBI TT buying | `SBI` | derived from SBI_TT_BUY history (a `0.00` row still counts as a publication day) |
| anything else | `WEEKDAY` | Monday to Friday |

A derived calendar needs no holiday list: inside the span of dates observed (400-day look-back, bounded by `known_at`), a day is a session only if it was observed, so gaps are that source's holidays or missed publications; outside the span, and when there is no history, weekdays are assumed. When the assumption applied, a `stale` note says so ("FBIL calendar not yet known, weekdays assumed"). Cost, accepted: a genuine holiday immediately after the newest observation reads `stale` for at most that day. Statement and manual prices have delay 0, so they are `stale` between statements (ADR-0015); `manual_prices` has no schedule of its own and its freshness is judged per asset in `as_of`, not per source. Missing on a holiday is `ok`; missing on a trading day is `stale` with both dates in the note.
- *Evidence:* `tests/test_dq.py::test_derived_calendars_follow_the_source_not_the_bse` (an observed BSE-holiday counts as a session; an unobserved BSE-open weekday inside the span does not), `test_a_gap_in_the_source_history_is_not_stale`, `test_assumed_calendar_is_reported_and_named_in_the_stale_note`; `tests/test_market_db.py::test_amfi_gap_is_a_holiday_even_when_the_bse_was_open`, `test_fbil_and_sbi_use_their_own_publication_days`.

**Fundamentals.** `valid_at` = period end. A row is visible to `as_of(valid_at, known_at)` when `recorded_at <= known_at` **and** `filed_at <= known_at` (a figure is not knowable before it is filed). More than 200 days between the period end and the query date reads `stale`. Concepts are a whitelist of nine statement lines ("download only what you need"); companyfacts is refreshed every 7 days.

**AMFI storage.** AMFI's terms forbid storing "any significant portion" of the site (V6). The adapter validates the whole file, then keeps only the lines for held schemes; the stored raw payload is that excerpt. NAVAll.txt carries one date per fetch, so history comes from CAS or a manual CSV; days missed while the machine is off are not recoverable from AMFI.

**Raw payloads** go to `data/raw/<source>/<date>-<sha12>.<ext>` (mode 600, git-ignored under `data/`), with sha256 and path in `raw_payloads`.

**Health.** The daily ingest is the canary: every run records success or failure per source. `GET /sources/health` (login required) reports state (`ok`, `stale`, `failing`, `no_data`, `not_consented`, `disabled`, `not_automated`), freshness, latest `valid_at`, calls today against the quota, and pending items. The registry stores `expected_delay_days` (integer) instead of an INTERVAL.

**Scheduling: coalesced catch-up (decision, kept; amends ADR-0019).** Missed daily slots are all recorded in `ref.job_runs` (unique per slot), but only the newest fetches: adapters fetch look-back windows (14 days FX, 10 days prices), so one fetch covers the gap, and eight fetches would only hammer the sources. Earlier slots are recorded as `coalesced_into`. Jobstore, timezone, `coalesce`, `max_instances` and the 7-day grace follow ADR-0019 unchanged; `ref.apscheduler_jobs` is created by migration 0003 because the app role cannot create tables.
- *Rationale:* with eight missed slots, eight identical fetches would hit sources that have rate limits and daily quotas (Tiingo 50/hour) and return the same data. *Evidence:* `test_market_db.py::test_job_slots_are_idempotent_and_missed_ones_are_caught_up` (eight slots recorded, exactly one fetch, a second catch-up does nothing, the same slot never runs twice), `test_scheduler_is_built_with_a_persistent_store_and_kolkata_time`, `test_a_crashing_job_is_recorded_as_failed`.

**DEMO.** DEMO market data is synthetic (`demo/market/`, generated by `scripts/make_demo_market.py`), served through the real adapters by `FixtureTransport`, so DEMO never touches the network. REAL market tables exist but stay empty until the Phase 4 gate.

**Fixtures and live capture (deferred).** The Tiingo and SEC test bodies stay documentation-derived and are labelled as such. Live capture waits until consent (`ARTHA_CONSENTED_SOURCES`), the Tiingo key and the SEC User-Agent are configured. Then: fetch once per source, save the raw payload (kept automatically in `data/raw/`), validate that the adapter parses it (a mismatch shows as `SCHEMA_DRIFT`), commit a trimmed, de-identified excerpt to `tests/fixtures/recorded/`, replace the hand-built bodies in `tests/test_adapters.py`, and close register item V23. Never commit a body that contains the key or the User-Agent contact detail.

## Consequences
Every threshold above is provisional, with one place to change it. Tiingo and SEC bodies in tests are hand-built from provider documentation, not recordings (V23): the first live fetch with the investor's key is the real test, and a shape mismatch surfaces as `SCHEMA_DRIFT`, not as bad data.
