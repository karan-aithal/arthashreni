# `make verify` design

Status: Phase 0 design, 2026-11-11. Implemented in Phase 1 (`scripts/verify.py`, ADR-0013); steps whose code does not exist yet run as "no targets" and pass, and each phase gate requires its steps to have real targets.

| # | Step | Tool / command | Fails when | Active from |
| --- | --- | --- | --- | --- |
| 1 | Lint + format check | `ruff check`, `ruff format --check` | any finding | Phase 1 |
| 2 | Types | `mypy --strict src/arthashreni` | any error | Phase 1 |
| 3 | Float ban | `scripts/check_no_float.py` (AST) in `ledger`, `analytics`, `policy`, `risk` | `float(`, a float literal or `float` annotation in money paths (none needed: XIRR is solved in Decimal, ADR-0024) | Phase 2 (active) |
| 4 | Import contracts | `lint-imports` (`import-linter`) | contract broken (below) | Phase 1 |
| 5 | Unit + integration tests | `pytest` with recorded HTTP fixtures (`respx`/`vcrpy`); network disabled (`pytest-socket`) | any failure, any real network call | Phase 1 |
| 6 | Property tests | `pytest -m property` (`hypothesis`, `ci` profile = 1,000 examples per test; normal runs use 150) | any counterexample | Phase 2 (active: ledger invariants) |
| 7 | XIRR oracle | own XIRR vs `pyxirr` vs LibreOffice `XIRR` via `soffice --headless` on shared cash-flow fixtures | any pair differs by > 1e-6, or one returns a value where another reports no solution | Phase 4 (active: `scripts/xirr_oracle.py`, 12 cases, LibreOffice 7.3 headless) |
| 8 | Golden fixtures | `pytest tests/test_golden.py` plus `tests/golden/derive_expected.py --check` (the committed `expected.json` must equal what the independent script derives) | computed ≠ expected, or expected out of date | Phase 2 (ledger, active), Phase 4 (calcs, active: `tests/golden/derive_calcs.py --check` + `tests/test_phase4_gate.py`) |
| 9 | Mutation testing | `mutmut` on `ledger`, `analytics`, `policy` | surviving ratio ≥ 10% | Phase 4 (active: `mutmut run` then `scripts/mutation_gate.py`; per package, mutants with no covering test count as surviving). Slow (minutes), so not in `--fast` / pre-commit |
| 10 | Branch coverage | `pytest --cov --cov-branch` | < 95% on `ledger`, `dq`, `analytics`, `policy`, `risk` | Per package from the phase that creates it |
| 11 | Tax-rule check | `scripts/check_tax_rules.py` | any `review_by` < today; any ledger transaction date with no valid rule for its asset class | First phase that adds `config/tax_rules.yaml` |
| 12 | Gate check | `scripts/check_gates.py` | Phase N files exist without `phase-(N-1).approved`; structural rules violated | Phase 1 |
| 13 | Security hygiene | `detect-secrets scan --baseline`, `pip-audit`, licence allow-list, dependency deny-list (ADR-0021: `kiteconnect` and other broker SDKs, `streamlit`, `pymupdf`/`fitz`, `google-api-python-client`, `google-auth-oauthlib`; before Phase 8: ollama/embedding/vector libs; before Phase 6: chart/web libs) | any finding | Phase 1 |

## Import-linter contracts
0. **Active contracts (Phase 4):** `analytics` imports no `db`, `ingestion`, `api`, `cli`, `portfolio`, sqlalchemy or web framework.
   **Phase 3:** `dq` imports no `db`, `ingestion`, `api`, `cli` or web framework; `db` also forbids `ingestion`.
   **Phase 2:** `core` imports nothing else; `db` and `ledger` and `ingestion` import no `api`, `cli` or web framework; `ledger` does not import `ingestion`. `ai`, `research`, `policy`, `risk` are added to these contracts in the phase that creates them (import-linter cannot name a module that does not exist yet).
1. **Core purity:** `arthashreni.ledger`, `.dq`, `.analytics`, `.risk`, `.policy` forbid `arthashreni.ai`, `arthashreni.research`, `arthashreni.ingestion.social`, `fastapi`, `starlette`, `flask`.
2. **No look-ahead:** `arthashreni.backtest`, `arthashreni.research` forbid `arthashreni.db.models.market` and `arthashreni.db.repositories`; only `arthashreni.db.as_of` allowed.
3. **No execution:** no module may import `*.execution`, `*.broker*`, `*.orders*` (also checked by path existence).
4. **Adapter isolation:** `analytics`, `risk`, `policy` forbid `arthashreni.ingestion`.

## Gate check structural rules (`check_gates.py`)
- Phase directory map: each package/path is tagged with its phase in `scripts/phase_map.yaml`; any tagged path present without the previous phase's approval fails.
- `apps/web/` or a chart/chat dependency present before `phase-5.approved` exists → fail.
- `ollama`, embedding or vector-store dependency before `phase-7.approved` exists → fail.
- Any social connector module → fail (MVP).
- `.approved` files are created only by the investor; the agent never writes them.

## Pre-commit
Steps 1, 2, 4, 5 (fast subset), 12, 13 (secrets only). Full run before each gate report.
