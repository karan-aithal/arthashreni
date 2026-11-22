# Phase 7 gate report — Research and IPO module

Date: 2026-11-22. Branch `phase-7-research-ipo` (from `main` at `8cf2bf7`). Nothing is committed yet. The agent stops here and waits for `docs/gates/phase-7.approved`; the agent never creates it.

## 1. Summary
- **Fund and ETF holdings** (look-through, overlap): one documented CSV (`isin,name,pct_to_nav`), imported with `holdings import`. Whole file or nothing; weights must total 100% ±1 point. Bitemporal and append-only: a corrected file is new rows. `build_report` now reads stored files; a file older than the latest month end that must be public is `stale` and so is the exposure computed from it.
- **News:** RSS/Atom files you save from official feeds, imported with `news import`. Parsed with `defusedxml`, markup stripped, lengths capped, untrusted by construction. The same headline on the same IST day is one event whichever feed carried it.
- **IPO research:** six checklist facts from the RHP, each with a source (`ipo-research fact`; `min_lot_value` sets the IPO's minimum application), then your classification with a reason (`ipo-research classify`). `ALLOWED` is refused unless the IPO is mainboard, PLANNED and the checklist is complete. Recording any new fact for an ALLOWED, still-PLANNED IPO sets it back to UNRATED (the ALLOWED row stays in history), so you must classify again against the updated checklist. Phase 5's `ipo_verdict` is unchanged: "can apply" also needs the sinking fund to cover the minimum.
- **Claims** (`claim add`) must point at stored evidence (news, IPO fact, fundamental, holdings row). **Fundamental ratios** (growth, margin) exist as pure `Measured` functions.
- Nothing fetches from the network. Nothing in the UI changed.

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Overlap reconciles on a fixture | pass | `test_overlap_reconciles_from_stored_files` and `test_overlap_of_two_funds_reconciles_by_hand`: stored weights add to 1; overlap of two fixture funds is 49.5% (Demo Stock min(40,25) + TREPS min(24.5,25)), worked out by hand; a CLI run gives 50.00% on a second fixture pair |
| Duplicate news collapses to one event | pass | `test_duplicate_news_collapses_to_one_event`: RSS imported twice and the same headline as Atom from another feed give 2 events (not 4); the duplicate lists both feeds, keeps the first link; same feed again changes nothing |
| IPO eligibility respects sinking fund and SME exclusion | pass | `test_ipo_needs_checklist_classification_and_money`: no minimum lot, incomplete checklist, UNRATED, and an under-funded reserve each block the application; it succeeds only with all four met. `test_sme_ipos_are_never_eligible`: an SME IPO is EXCLUDED, cannot be ALLOWED, cannot be blocked, even with a complete checklist |
| Data quality travels | pass | stale holdings file gives `stale` exposure (`test_look_through_uses_isin_for_direct_holdings_and_marks_stale`); no file gives "no holdings data" and `missing` overlap, never a guess |
| Append-only, demo isolation | pass | `fund_holdings`, `ipo_research`, `ipo_classifications`, `research_claims` reject UPDATE, DELETE and TRUNCATE; REAL tables stay empty |
| Untrusted content | pass | entity-expansion XML bomb rejected, script text becomes inert text, non-http links and undated or untitled items rejected |

## 3. `make verify` result
Unabridged: `docs/gates/phase-7.verify.log`. All steps passed.
```
ruff check / ruff format / mypy --strict (111 files): ok
no float in money packages (now incl. research): ok     import contracts: 11 kept, 0 broken
gate check / secret scan: ok            tests: 711 passed, 1 skipped (total coverage 98%; research 100%)
web: lint / typecheck / tests (38 passed) / build: ok   property tests (1000 examples), golden, XIRR oracle: ok
dependency audit (python and web): ok
mutation survivors (limit 10%): analytics 1.1%, ledger 1.2%, policy 1.8%, risk 0.1%, reports 0.0%, research 0.6%
```
Two things went wrong on the way and were fixed, not worked around: the Phase 6 UI fixture `portfolio.json` changed (below), and `research` first showed 15% surviving mutants; I added exact assertions and simplified redundant code rather than loosening the limit.

## 4. Changes
- `src/arthashreni/research/` (pure): `holdings.py`, `news.py`, `ipo.py`, `fundamentals.py`. `research_store.py` (database), `cli_research.py` (CLI), `migrations/versions/0006_research.py`.
- `analytics/allocation.py`: `look_through` takes `stale_funds`; direct holdings are keyed by ISIN when known. `analytics/valuation.py`: `AssetInfo.isin`. `portfolio.py`: loads stored holdings.
- `pyproject.toml`: new import-linter contract for `research`; `research` added to the other packages' forbidden lists; `defusedxml` dependency and `types-defusedxml` (dev). `scripts/`: `research` added to coverage, no-float and mutation gates.
- Source registry and `feasibility.md`: holdings and RSS rows now say "manual import, terms unchecked".
- Docs: ADR-0030, verify register V35 and V36, README, MVP scope, data model.

## 5. Run commands
```
make migrate                                   # applies 0006
uv run arthashreni holdings import <ISIN|AMFI:code|TICKER:MIC:sym> file.csv --as-of 2026-10-31
uv run arthashreni holdings overlap <fund A> <fund B>
uv run arthashreni news import feed.xml --feed rbi_press
uv run arthashreni ipo add REF --board MAINBOARD
uv run arthashreni ipo-research fact REF min_lot_value 14800 --source "RHP p.12"
uv run arthashreni ipo-research checklist REF
uv run arthashreni ipo-research classify REF allowed --reason "..."
uv run arthashreni claim add SUBJECT "text" --kind NEWS --evidence-id 1
```

## 6. Decisions (ADR-0030; reversible, none touches Part A)
1. Manual holdings CSV and manual news files: no fetcher, because AMC and feed terms are unchecked (V35, V36) and D1 allows manual import.
2. Same normalised headline on the same IST day is one event; reworded headlines are separate events.
3. The IPO checklist (six facts) is this project's minimum, not a regulatory list. The system never classifies an IPO itself.
4. Holdings staleness uses a 10-day publication lag after month end (the SEBI rule as understood, not re-read from the circular).
5. `research` joined the coverage, no-float and mutation gates, beyond the packages G1 names. Say if you want that reversed.

## 7. Known limitations
- **UI shows ISINs in look-through.** Direct holdings and fund underlyings are keyed by ISIN, so the Portfolio page's look-through list shows ISINs instead of names. `apps/web/fixtures/portfolio.json` was regenerated (new `isin` field and keys); web tests pass. A name lookup would be a small follow-up.
- Underlying lines without an ISIN (cash, TREPS) are keyed by name and never merge with direct holdings, so exposure is understated, not invented.
- No commodity-driver research, no US ETF issuer files, no per-AMC parsers, no fetchers, no news-to-asset linking, no fuzzy duplicate matching, no research pages in the UI.
- Fundamental ratios are not wired into any report.
- Evidence existence checks for `FUNDAMENTAL` claims read the fundamentals table by id (not a historical read, so no look-ahead risk, but it is a direct table read in the service layer).
- Phase 5/6 limitations stand (macro feeds missing, V30, V31, V34).

## 8. Proposed next phase (Phase 8 — Local LLM layer)
Ollama (CPU), Qwen3 small model benchmark, placeholders filled by code, numeric-claim verification, evidence-ID citation (the `research_claims` and `news_events` ids are the evidence), injection fixtures, template fallback, call logging. Gate: injected instructions in news text change nothing; a literal number not in the input is rejected; the report works with the LLM off.

**Reminders (investor action):**
- Create `docs/gates/phase-7.approved` only after you have tried the commands above on your own files.
- Run `make migrate` before using the new commands on your real database.
- Prepare holdings CSVs from the fund houses' monthly disclosures yourself; check the terms of each source (V35, V36).
- The Phase 5 reminders still stand: write your IPS, fill `config/costs.toml`, rotate the `artha_owner` password.
