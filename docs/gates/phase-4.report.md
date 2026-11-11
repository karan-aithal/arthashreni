# Phase 4 gate report — Calculation engine

Date: 2026-11-11. Branch `phase-4-calculation-engine`. Agent stops here and waits for `docs/gates/phase-4.approved`. Approving it also unlocks the REAL dataset (`RealDataLocked` is lifted); nothing real has been imported or touched.

## 1. Summary
- `analytics` package (pure, `Measured` in and out, Decimal only, 40-digit context): XIRR, TWR, absolute return, CAGR, positions and valuation (INR and original currency, FX with a named rate type), realised and unrealised P&L, income and costs, FX attribution, allocation (class, country, currency, sector, market cap, holding), concentration (top-N, HHI), look-through and overlap, volatility, drawdown with recovery time, correlation, benchmark comparison against a documented benchmark list.
- XIRR in exact Decimal (ln-rate bracketing and bisection), no float anywhere. It refuses instead of guessing: no sign change, one date, no root in range, several roots, a tangent root. `ok` is given only when Descartes proves the root unique (flows change sign once); anything else is `estimated` or `missing` (see the amendment below).
- `src/arthashreni/portfolio.py` is the only DB-touching glue (`build_report`); `GET /portfolio` and `arthashreni portfolio summary | allocation | risk-stats` show every number as value + status + as-of + notes.
- Oracle: ours vs `pyxirr` vs LibreOffice `XIRR` (headless) agree on 12 cases. Golden: DEMO valuation, P&L, income and costs, XIRR (portfolio and per asset), FX attribution, allocation and HHI re-derived independently in `tests/golden/derive_calcs.py`.
- Mutation testing active on `analytics` and the pure `ledger` modules. Coverage and mutation thresholds met.
- **No live source was contacted. No real data exists.** Everything ran on DEMO data.

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Oracle comparison: XIRR vs `pyxirr` vs LibreOffice within 1e-6 | pass | `scripts/xirr_oracle.py` (in `make verify`): 12 cases (single lump, two contributions, 12 and 24 monthly SIPs, loss, leap day, dividends, same-day flows, zero return, buy/part-sale/buy, and two no-solution cases where all three report none). Largest disagreement seen: ~3e-10 (pyxirr is float). LibreOffice 7.3.7.2 really computed the values (V28). Multiple-root cases are excluded on purpose: ours refuses, the others return one root. Property test `test_agrees_with_pyxirr_when_the_root_is_unique` adds 1,000 random unique-root flows against pyxirr |
| Golden fixtures | pass | `tests/golden/derive_calcs.py --check` (stale file fails `make verify`) and `tests/test_phase4_gate.py`: values (to 4 dp, exact), per-asset and portfolio XIRR (1e-8 / 1e-9; the script uses float bisection on the rate, a different method), FX attribution, weights, HHI. The golden script imports nothing from `arthashreni` |
| FX attribution reconciles | pass | `FxAttribution.reconciles` (residual <= 1e-12): INR return = local + FX + interaction and the three INR amounts sum to the holding's unrealised P&L; DEMO DTECH residual 0E-36. Property test over random costs, values and rates; names the valuation rate type (`FBIL_REF`) |
| Mutation threshold (surviving < 10%) | pass | `mutmut` 3.8.1, per package: `analytics` 128 of 1,801 not killed = **7.1%**, `ledger` (pure modules) 19 of 411 = **4.6%**; `policy` does not exist yet. `scripts/mutation_gate.py` counts "no tests" as surviving. Remaining survivors are mostly equivalent mutants (`events.sort(key=None)`, the quantum of `Decimal(2).scaleb(-n)`, internal loop bounds) and a few `missing()` notes whose `as_of` is the wall clock |
| Branch coverage >= 95% | pass | `analytics` 99%, `ledger` 99%, `dq` 97%; whole project 97% (365 tests) |
| import-linter contracts | pass | 6 kept, 0 broken (new: `analytics` imports no `db`, `ingestion`, `api`, `cli`, `portfolio`, sqlalchemy or web framework) |
| No float in money code | pass | `check_no_float.py` covers `analytics`; the "documented XIRR boundary" exception was not needed |
| mypy --strict | pass | 78 files; a bare number where a price or FX rate is expected is a type error |
| Data quality travels | pass | stale DTECH close (2026-11-09 vs expected 2026-11-10) makes its value, P&L, the totals and its XIRR `stale`, with both dates in the note; a holding without a price is left out of totals and weights, named, and the sums become `estimated` |

## 3. `make verify` result
```
-- ruff check / ruff format / mypy --strict (78 files): ok
-- no float in money packages: ok
-- import contracts: ok            (6 kept, 0 broken)
-- gate check: ok
-- secret scan: ok
-- tests: ok                       (365 passed)
-- branch coverage >= 95% (ledger, dq, analytics, policy, risk): ok     ledger 99%, dq 97%, analytics 99%
-- property tests (1000 examples): ok
-- golden fixtures / golden values reproducible: ok
-- XIRR oracle (ours vs pyxirr vs LibreOffice): ok
-- golden calculations reproducible / golden calculations: ok
-- tax-rule check: skipped (no tax rules yet)
-- dependency audit: ok            (pip-audit: no known vulnerabilities)
-- mutation: run mutants: ok       (about 130 s on 8 workers; whole `make verify` about 4 min)
-- mutation: survivors < 10%: ok   analytics 7.1%, ledger 4.6%
verify: all steps passed
```

## 4. Changes
```
src/arthashreni/analytics/{xirr,returns,flows,valuation,fx,allocation,series,benchmarks}.py   (+ PRECISION in __init__)
src/arthashreni/portfolio.py   api/portfolio.py   api/serialize.py   cli.py (portfolio group)
src/arthashreni/dq/measured.py (day_stamp)   db/as_of.py (price_series, valuation_fx_typed)
scripts/{xirr_oracle,mutation_gate}.py   scripts/verify.py   scripts/make_demo_market.py
tests/{test_xirr,test_analytics,test_analytics_exact,test_analytics_properties,test_ledger_exact,test_portfolio_twr,test_phase4_gate}.py
tests/golden/{derive_calcs.py,expected_calcs.json}   tests/conftest.py   tests/test_phase3_gate.py (price count)
demo/portfolio/assets.csv (+ DEMO benchmark series asset)   demo/market/manual_prices.csv (+ DIPO, DNEW, benchmark prices)
pyproject.toml (analytics contract, [tool.mutmut], dev deps pyxirr and mutmut)   .gitignore (mutants/)
docs: ADR-0024, architecture, runbook, verify-design, verify-register (V16 updated, V28-V30)
```
- **No migration.** No table or column changed. DEMO assets gained one never-held benchmark series (`NIFTY50TRI`), and DEMO prices gained history for two held assets so the DEMO portfolio is fully priced.
- **API:** `GET /portfolio?as_of=` (login required, read-only). **CLI:** `portfolio summary | allocation | risk-stats`.
- **Dependencies added (dev only):** `pyxirr` 0.10.8 (Unlicense), `mutmut` 3.8.1 (BSD-3; pulls `textual`, `libcst`). No runtime dependency added. No denied package.
- **Test infrastructure:** the throwaway database is now created only for modules that need it (`NO_DB_MODULES` in `tests/conftest.py` lists the DB-free ones); this is what lets mutation workers run in parallel safely (V29).

## 5. Run commands (Linux)
```bash
uv run arthashreni demo seed && uv run arthashreni demo market
uv run arthashreni portfolio summary --date 2026-11-10
uv run arthashreni portfolio allocation --date 2026-11-10
uv run arthashreni portfolio risk-stats --date 2026-11-10
uv run python scripts/xirr_oracle.py
make verify
```

## 6. Decisions
Conventions chosen without asking are in **ADR-0024** (Accepted, reversible): which cash flows count, TWR and absolute-return definitions, how partial valuations are labelled, the FX-effect definition (broker markup included), series rules, benchmarks, look-through. Points worth your attention:
1. **Gain and absolute return are before tax.** No tax rule exists, so "tax unavailable" (C7). Realised P&L is FIFO, pre-tax.
2. **FX effect includes the broker's markup.** Purchase rate is the rate INDmoney applied; valuation uses FBIL (ADR-0016). The split between true currency movement and conversion cost is not separated.
3. **TWR is `missing` for DEMO** because DEMO prices start in 2026 and the demo ledger in 2024. It is proven on a small hand-made ledger (`tests/test_portfolio_twr.py`: TWR 21%, XIRR and absolute return checked against hand and `pyxirr`).
4. **Asset-level XIRR is `estimated`** when units moved without cash (reinvested dividends, switches, transfers in). A merger folds the predecessor's flows into the successor.
5. **Benchmarks need your data.** No compliant free index source is in the registry; you register an index as an asset and import levels (`market import-prices`). DEMO has a synthetic Nifty-like series, none for the S&P, so the US holding's comparison is `missing`.
6. **A mutation result of 100% killed on the first run was false:** parallel workers collided on one test database, so tests errored and counted as kills. Caught by checking individual mutants; fixed by DB-free selection (V29).

## 7. Known limitations
- **Portfolio-level volatility and drawdown are not built** (holding-level is). They need a daily price-and-FX panel; planned with the risk engine (Phase 5).
- **Look-through and overlap are pure functions with no data**: fund-house holdings arrive in Phase 7, so every fund is listed as "no holdings data".
- **Tax, inflation-adjusted returns and per-account returns are not built.**
- **TWR needs a price on every cash-flow date.** Statement-only prices (Indian ETFs and stocks, V4) mean TWR is often `missing` until prices are imported for those dates.
- **Mutation testing covers `analytics` and the pure `ledger` modules** (the DB-bound ledger modules are covered by DB tests but not mutated); `policy` does not exist yet. The 10% limit is per package.
- XIRR search range is -99.9% to +1,200,000% a year; outside it the result is `missing`.
- DQ thresholds from Phase 3 remain provisional (V27). With real history imported after this gate, recalibrate them (the earliest point that allows it).

## 8. Proposed next phase (Phase 5 — Budget engine, policy, risk and template report)
Scope: B1–B6 (budget envelope enforced by database constraints, SIP mandates, reserves, IPO sinking fund, cost rule, IPS and personal context) and F2–F3 (risk signals and four levels, stress scenarios, de-duplicated alerts, decision engine with hard gates, vetoes, advisory score, recommendation JSON) and the template monthly report; migration for the envelope, reserves and IPS tables; `risk`, `policy`, `reports` packages with their import contracts. Gate: the property tests of B7 (total <= budget, envelope reconciles, no double allocation, reserves never negative, no SME IPO eligible, every line obeys the IPS bands), double-allocation concurrency test, risk thresholds documented with rationale.
Risks: the IPS is yours to write (no personalised allocation without it); no macro feed exists yet (FRED/RBI), so market-wide signals will be `missing` where data is absent; portfolio-level history is needed for portfolio signals.

**Reminders (investor action):**
- Rotate the `artha_owner` password that a malformed command echoed in part (Phase 1): delete the `ARTHA_DB_*` and `ARTHA_DATASET` lines from `.env`, then run `bash scripts/db_setup.sh`.
- Before importing real statements, the first import should be previewed and read by you (V21, V15: parser formats not yet confirmed on real files).

## 9. Amendment after review (XIRR proof status, TWR, volatility, correlation)
Review asked for the XIRR gate to be checked against negative rates and tangent roots. **The first version overstated Norstrom's criterion; that was a real defect and is fixed.**

| Review item | Result |
| --- | --- |
| Norstrom proof status against negative IRRs | **Was wrong.** The code and ADR claimed one sign change in the running total proves a unique root for every rate above -100%. It proves it only above 0%. Searched 6,000 random patterns: 493 had a single running-total sign change and two roots, one negative. Fixed: only Descartes (flows change sign once) earns `ok`; Norstrom-only patterns are `estimated` with the note "unique above 0% by Norstrom; negative rates checked by scan only". The theorem as restricted (at most one root above 0%, total not zero) is property-tested |
| Two-root Norstrom counterexample | Added: flows -8, -1, +28, -13 at years 0, 3, 4, 5 (running total -8, -9, +19, +6; roots about -52.8% and +16.2%) and a second (+13, -15, -20, +17). Both now return `missing`, "2 different rates ... (multiple roots)". Before the fix the first would have returned 16.2% |
| Even-multiplicity root regression | Added: +1, -2.2, +1.21 (NPV = (1 - 1.1/(1+r))^2, tangent at exactly 10%) gives `missing` "the only root has even multiplicity", also with signs negated; and a double root at 10% next to a crossing at 50% gives `missing` (a sign-change scan used to return 50%). Implemented by refining every local extremum of the NPV between grid points; two crossings inside one grid step are also found (tested) |
| Multiple-root inputs cannot yield an apparently valid single root | Property test, 1,000 random sign patterns against an independent 0.01-grid root counter: two or more crossings always give `missing`; `ok` only when the flows change sign once; a returned value always matches the one independent root. Remaining honest limit: a Norstrom-only or unproved pattern with exactly one root found is returned `estimated`, never `ok`, and two stationary points inside one 0.05 log-rate step would not be resolved |
| TWR external-flow semantics | Documented in ADR-0024 and `flows.twr_flow`: purchase/IPO block positive; sale/redemption/IPO refund negative; dividend/interest negative (income leaves the holdings, so it counts as return); standalone fee/tax positive (lowers the return); deposits, withdrawals, FX conversions and cash-less unit moves are not flows. Tested: a 50 dividend on 1,000 is +5%, a 10 fee is -1%, a purchase or sale that exactly matches the value change is 0% |
| Daily-observation assumption for annualised volatility | Was an unchecked assumption (sqrt(252) on any series). Now checked: median gap above 5 calendar days is refused (`missing`, "not a daily series"); a daily series with longer gaps is `estimated` with the count. Boundary tests at 5 and 6 days |
| Correlation uses matching observation intervals | Verified by construction and by test: both series are reduced to shared dates first, so each pair of returns spans the same interval. A daily series against an every-third-day series equals the correlation on the shared dates; spiking days only one series has changes nothing (also for the benchmark comparison); pairing by position gives a different number |

Effect on earlier results: DEMO golden values and the oracle are unchanged. Per-asset or portfolio XIRRs whose flows change sign more than once (partial sale then re-buy, SIP then redemption) now say `estimated` where they said `ok`. Reopened nothing else: golden calculations, FX attribution, Decimal and type contracts, coverage, API/CLI and import boundaries are as reported. The mutation run was repeated on the corrected code (the figures above).

## 10. Amendment: tangent root on the search grid

A tangent (even-multiplicity) root that the grid lands on exactly (for example flows +1, -2, +1 a year apart: double root at 0%) was counted as an ordinary crossing and returned as an `estimated` rate. An exact zero on the grid now counts as a crossing only if the NPV changes sign across it; otherwise it is counted as a tangent root and the result is `missing` with the even-multiplicity reason. Three tests added (lone grid-aligned tangent, tangent beside a crossing, real crossing on the grid beside a second crossing); the first failed before the fix.

Full `make verify` output after the fix, unabridged: `docs/gates/phase-4.verify.log` (357 tests, 1000-example property run, oracle 12 cases 0 disagreements, mutation analytics 6.9% / ledger 4.6%, dependency audit, all steps passed).

### Upper search bound (investor-approved fix)

The contract (ADR-0024, report §8) is a search range of -99.9% to +1,200,000% a year. The code searched up to x = ln(1 + r) = 14, i.e. r = e^14 - 1 = 1,202,603 as a fraction, which is **+120,260,300%**: the docs were right, the constant was 100 times too wide (it read 1.2 million as a fraction, not as a percent). The upper bound is now ln(1 + 12,000) (plus 1e-30 of slack so a root at exactly +1,200,000% stays inside), and `_grid` adds that point because it is not on the 0.25 / 0.05 step grid. A root beyond it is `missing` with the existing "no root between -99.9% and +1,200,000% a year" note; nothing is clamped. The lower bound e^-7 - 1 = -99.909% is what "-99.9%" always meant and is unchanged.

Tests: rate 11,999 and 12,000 (the limit itself) return `ok` with the right value; 12,001 and 120,000 return `missing`. An older test that called +99,999,900% "inside the search range" encoded the wrong range and was corrected (+1,099,900% inside, +99,999,900% outside).

Final `make verify`: 365 tests, oracle 12 cases 0 disagreements, mutation analytics 7.1% / ledger 4.6%, all steps passed; unabridged output in `docs/gates/phase-4.verify.log`.

## 11. Amendment: precision, partial-valuation TWR, median gap

Done in the order the reviewer set; the grid-aligned tangent root (item 1) is section 10.

1. **Precision.** The 40-digit `PRECISION` now lives in `dq/measured.py` (`analytics.PRECISION` re-exports it) and every `Measured` arithmetic helper uses it. Three paths had run in the caller's ambient 28-digit context: the `twr` chain (including the final `growth - 1`), `positions_from_book` (original-currency cost) and all `m_add/m_sub/m_mul/m_div` calls (valuation, totals, absolute return). New test `test_results_do_not_depend_on_the_callers_decimal_context` runs twr, absolute return, CAGR, positions and valuation under precisions 5, 12, 28 and 80 and requires identical results; it fails if the context in `_combine` is removed. Four existing tests compared against 28-digit divisions and now use a 40-digit reference. Not changed: ledger arithmetic (separate contract, quantised) and sums of ledger amounts (exact up to 28 digits).
2. **Partial-valuation TWR: decision, refuse.** `ValuePoint.complete` is False when the valuation left out holdings with no price; `twr` then returns `missing` naming the dates (first three). The alternative, an `estimated` figure, was rejected: a holding priced on day A, absent on day B and priced again on day C reads as a loss then a gain, compounding into a wrong number that a label does not repair. XIRR with a partial ending value stays `estimated` with its note, since one end value is a bounded understatement. Tests: unit (`test_twr_refuses_a_partial_valuation_instead_of_chaining_through_it`) and end to end on a database ledger (`tests/test_portfolio_partial_twr.py`).
3. **Median gap.** `volatility` used `gaps[len // 2]`, the upper middle element for an even count, which could refuse a daily series whose true median is within the limit (12 gaps of 1 and 12 of 7: median 4, was rejected as 7). It now uses `statistics.median`; the refusal note prints a half-day median as such (5.5). Tests cover medians of 4, 5 and 5.5.

`make verify`: 365 tests, oracle 12 cases 0 disagreements, mutation analytics 7.1% / ledger 4.6%, all steps passed; output in `docs/gates/phase-4.verify.log`.
