# Phase 5 gate report — Budget engine, IPS, risk engine, decision engine, template report

Date: 2026-11-12. Branch `phase-5-budget-policy-risk` (from `main` after the Phase 4 merge). Nothing is committed yet. The agent stops here and waits for `docs/gates/phase-5.approved`; the agent never creates it.

## 1. Summary
- **Migration 0004** adds every Phase 5 table to `demo` and `real`: budget settings, SIP mandates, budget periods, envelope lines, reserves and their ledger, IPS versions, personal context, IPO applications, recommendations and their lines, decisions, risk signals, levels and alerts. Over-allocation is a database error: a CHECK on the four envelope columns, a deferred trigger on the line sums, a frozen allocated month, `UNIQUE(envelope_line_id)` on recommendation lines, and a trigger that refuses any reserve entry that would make a balance (or blocked IPO money) negative.
- **`policy`** (pure, except `store.py`): envelope, IPS, personal context and hard liquidity rules, cost rule, IPO eligibility, advisory score, recommendation schema, decision engine. **`risk`** (pure, except `store.py`): signals, four levels, market-wide vs asset-specific classification, vetoes, stress scenarios, de-duplicated alerts; thresholds in `config/risk_thresholds.toml`. **`dq/confidence.py`**: the computed confidence of ADR-0010 (specified in Phase 3, never built). **`reports/monthly.py`**: a Markdown report from templates, no LLM. **`review.py`**: the service layer (`run_risk`, `monthly_review`).
- **Decision order** (F3): hard gates, policy allocation in tiers (below band low, then below mid), risk vetoes, advisory score (orders inside a tier only), cost rule, envelope validator. Committed SIPs are deducted first. A sale is never implied.
- **Surfaces:** CLI groups `budget`, `sip`, `reserve`, `ips`, `context`, `ipo`, `risk`, `review`, `demo policy`; read-only API `GET /risk` and `GET /review`; the scheduler runs the daily risk step after the ingest and a draft monthly review on the first weekday of the month. **Nothing approves or executes anything**: the investor's decision is a recorded row.
- **No live source was contacted. No real data exists.** Everything ran on DEMO data. The DEMO policy lives only in the `demo` schema (tested).

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Total recommended money <= budget; envelope reconciles exactly | pass | Property test `test_property_budget_envelope_and_lines` (1,000 examples in `make verify`): committed + new + reserve transfers + unallocated = opening, none negative, committed = sum of SIPs. Database CHECK and trigger tests in `tests/test_policy_db.py` |
| No envelope line used twice | pass | Same property (every envelope line has exactly one recommendation line); `UNIQUE(envelope_line_id)` tested; second allocation of a month raises `PeriodAllocated` |
| **Double-allocation concurrency test** | pass | `test_two_concurrent_allocations_of_one_month_leave_one_winner_and_exact_totals`: two threads, a barrier, four months; exactly one `ok`, lines and totals exact. Mechanism: `SELECT ... FOR UPDATE` on the month's row, triggers as backstop (ADR-0025) |
| Reserves never negative | pass | Property `test_property_reserves_never_go_negative`; database trigger refuses a negative balance and over-release of blocked IPO money (tested) |
| No SME IPO eligible | pass | Property tests: any board other than `MAINBOARD` is never eligible; mainboard needs reserve cover and `ALLOWED` research. SME rows are `EXCLUDED` (CHECK) |
| Every recommendation obeys the IPS bands | pass | Property `test_property_every_allocation_obeys_the_ips`: after SIPs and new money, class, country, holding, sector and fund-house limits hold; no money for vetoed, failed-thesis, unusable-data or unbanded assets; cooldown; mix-change limit; unstable-income rule; expense window; cost rule |
| Risk thresholds documented with rationale | pass (provisional, **not backtested**) | `config/risk_thresholds.toml`: every row has a unit, direction, rationale and `backtested = false`; a test fails otherwise. ADR-0026, V31. Backtesting is not possible without history |
| No personalised allocation without an approved IPS | pass | `test_without_an_approved_ips_nothing_personalised_is_produced`: only SIPs, a "what is missing" section; the recommendation validator forbids it |
| Mutation: surviving < 10% | pass | `mutmut` per package: `analytics` 136 of 1,668 = **8.2%**, `ledger` 18 of 411 = **4.4%**, `policy` 21 of 627 = **3.3%** (pure policy modules; `policy/store.py`, `risk`, `reports` are DB- or report-bound and are covered by DB tests, not mutated) |
| Branch coverage >= 95% | pass | `policy` 99%, `risk` 100%, `dq` 97%+; guarded packages together 99% |
| import-linter | pass | 10 kept, 0 broken (new: policy/risk/reports purity, risk below policy, no HTTP or LLM client in policy, risk, reports; lower layers never import them) |
| mypy --strict, ruff, no float, secret scan, dependency audit | pass | 101 source files |
| Gate check | pass | `phase-4.approved` exists; no `apps/web`, no LLM or chart dependency |

## 3. `make verify` result
Unabridged output: `docs/gates/phase-5.verify.log`.
```
-- ruff check / ruff format / mypy --strict (101 files): ok
-- no float in money packages: ok
-- import contracts: ok            (10 kept, 0 broken)
-- gate check / secret scan: ok
-- tests: ok                       (593 passed, 1 skipped)
-- branch coverage >= 95%: ok      policy 99%, risk 100%
-- property tests (1000 examples): ok
-- golden fixtures / golden values / golden calculations: ok
-- XIRR oracle: ok
-- tax-rule check: skipped (no tax rules yet)
-- dependency audit: ok
-- mutation: survivors < 10%: ok   analytics 8.2%, ledger 4.4%, policy 3.3%
verify: all steps passed
```

## 4. Changes
```
migrations/versions/0004_budget_policy_risk.py
src/arthashreni/policy/{envelope,ips,context,cost,ipo,score,recommendation,decision,store}.py
src/arthashreni/risk/{thresholds,signals,levels,scenarios,alerts,store}.py   config/risk_thresholds.toml
src/arthashreni/dq/confidence.py   reports/monthly.py   review.py   cli_policy.py   api/review.py
src/arthashreni/{cli,jobs,demo}.py   api/{app,serialize}.py   demo/costs.toml
tests/{test_confidence,test_policy_envelope,test_policy_rules,test_policy_decision,test_risk,test_policy_db,test_phase5_gate}.py
pyproject.toml (contracts, mutmut, per-file lint ignores)
docs: ADR-0025, ADR-0026, data-model, architecture, verify-design, runbook, verify-register (V10 re-checked, V31-V34)
```
Earlier-phase files touched, and why: `tests/test_market_db.py` (the scheduler now stores two jobs); `tests/test_xirr.py` (one Phase 4 property test failed once on a Hypothesis "filtering too much" health check, seed-dependent and unrelated to this phase; that test discards most patterns on purpose, so the check is suppressed for it); `tests/conftest.py` (database teardown retries after a garbage collection: dropping the throwaway database was flaky when a worker-thread connection had not finished closing); `api/serialize.py` (a risk level is shown by name, not rank). No new runtime or dev dependency.

## 5. Run commands (Linux)
```bash
uv run arthashreni demo seed && uv run arthashreni demo market && uv run arthashreni demo policy
uv run arthashreni risk run --date 2026-11-10
uv run arthashreni review run --period 2026-11-01 --date 2026-11-10        # dry run, stores nothing
uv run arthashreni review run --period 2026-11-01 --date 2026-11-10 --commit
uv run arthashreni review decide <recommendation id> --decision APPROVE --reason "..."
uv run arthashreni ips template           # an example only: the bands are yours
```
See `docs/runbook.md` ("Budget, risk and the monthly review") for the REAL setup (budget, SIPs, reserves, context, IPS, costs).

## 6. Decisions (ADR-0025, ADR-0026; reversible, none touches Part A)
1. **No UNALLOCATED envelope line**; `RESERVE_USE` sits outside the month's identity. One recommendation per month; `review run` without `--commit` stores nothing, `--commit` stores once.
2. **Unknown emergency fund blocks new money** ("insufficient evidence"); unconfirmed income stability is treated as unstable (the risky share may not rise).
3. **No platform fee is hardcoded.** Without your `config/costs.toml`, every investment is "cost unknown" and goes to the cash reserve.
4. **Confidence is `low` whenever nothing was cross-checked** (agreement defaults to 0, ADR-0010). That is the case for all DEMO data and will be for early real months, so expect `low` until prices are cross-checked.
5. **Band breaches count once** toward the risk level (an overweight class is also an overweight country and currency).
6. **Asset-specific declines are labelled "uncertain", not "structural"**, because fundamentals and filings do not exist before Phase 7.
7. **Approving a reserve transfer or reserve use writes the reserve ledger once**: your own bookkeeping, not a trade.

## 7. Known limitations
- **Risk thresholds are provisional and not backtested** (V31). Recalibrate after real history is imported.
- **Missing signals:** rates, inflation, oil (no FRED/RBI/MOSPI adapters, V34), index breadth, overlap (no fund holdings until Phase 7). On DEMO data the level rests on about a third of its signals, and the report says so. Index and S&P/gold/silver benchmark levels must be imported by you (V30); DEMO has only a Nifty-like series.
- **Mutual funds are treated as equity-oriented** (V32): a debt fund would be overstated in scenarios and counted as risk money.
- **Theme limits cannot be enforced** (assets have no theme attribute). **Tax is unavailable** (no verified rules, C7), so a sale line says so. **V16** (stamp duty, NAV applicability) is still open.
- **Mutation testing covers the pure `policy` modules** only; `policy/store.py`, `risk` and `reports` are tested with DB and report tests (coverage 96% to 100%) but not mutated, because mutation workers cannot share a database (V29). Analytics at 8.2% is close to the 10% limit.
- **No notification channel** (desktop notification, email, Telegram) exists: alerts are stored and shown in the CLI, API and report. The monthly job fires on the first weekday and does not model exchange holidays. Ledger events for IPO block, allotment and refund are recorded on the reserve side; the matching `IPO_*` ledger transactions still arrive with statement imports.
- **Reserve use and costs are only as good as your inputs** (reserve balance, `config/costs.toml`).
- SEBI's final SME rule: the register notes that no final circular was found by web search (secondary sources only), and SME IPOs stay excluded either way (V10).

## 8. Bugs the tests found (all fixed, with regression tests)
1. **Reserve use skipped the IPS limits and the cost rule** (property test): it now goes through the same room, data-gate and cost checks as new money.
2. **An IPS with no applicable limit made the room infinite** and the mix-change check crashed with `InvalidOperation` (mutation tests): the room now takes the money available as a ceiling.
3. **The IPS template printed by `ips template` was itself invalid** (bands could not sum to 100%): fixed and covered by a CLI test.
4. `check_cost` divided zero by zero for a zero amount: it now refuses a non-positive amount.

## 9. Proposed next phase (Phase 6 — Read-only UI)
Scope per `docs/mvp-scope.md`: `apps/web/` with read-only pages over the existing API (portfolio, allocation, risk, the monthly review and its report, data sources and quality, quarantine queue view), DEMO visibly labelled, every number with status and as-of; first chart library and web dependencies (allowed only once `phase-5.approved` exists). Gate: accessibility basics, no write endpoint from the UI, DEMO label on every page.
Risks: the approval workflow stays in the CLI until Phase 9; the UI cannot show more than the API provides (signals marked missing stay missing).

**Reminders (investor action):**
- Phase 6 may not start until you create `docs/gates/phase-5.approved`.
- Write your own IPS (`ips template` is an example only) and put your platforms' real fees in `config/costs.toml` before the first real monthly review; until then the real review will show only what is missing.
- Rotate the `artha_owner` password that a malformed command echoed in part (Phase 1): delete the `ARTHA_DB_*` and `ARTHA_DATASET` lines from `.env`, then run `bash scripts/db_setup.sh`.
- Before importing real statements, preview and read the first import yourself (V21, V15). Recalibrate the DQ thresholds (V27) and risk thresholds (V31) once real history exists.
