# `make verify` design

Status: Phase 0 design, 2026-11-11. Implemented in Phase 1 (`scripts/verify.py`, ADR-0013); steps whose code does not exist yet run as "no targets" and pass, and each phase gate requires its steps to have real targets.

| # | Step | Tool / command | Fails when | Active from |
| --- | --- | --- | --- | --- |
| 1 | Lint + format check | `ruff check`, `ruff format --check` | any finding | Phase 1 |
| 2 | Types | `mypy --strict src/arthashreni` | any error | Phase 1 |
| 3 | Float ban | ruff banned-API / AST check in `ledger`, `analytics`, `policy`, `risk` | `float(` or float literal in money paths (documented XIRR boundary excepted) | Phase 2 |
| 4 | Import contracts | `lint-imports` (`import-linter`) | contract broken (below) | Phase 1 |
| 5 | Unit + integration tests | `pytest` with recorded HTTP fixtures (`respx`/`vcrpy`); network disabled (`pytest-socket`) | any failure, any real network call | Phase 1 |
| 6 | Property tests | `pytest -m property` (`hypothesis`, fixed CI profile ≥ 1,000 examples per test) | any counterexample | Phase 2 |
| 7 | XIRR oracle | own XIRR vs `pyxirr` vs LibreOffice `XIRR` via `soffice --headless` on shared cash-flow fixtures | any pair differs by > 1e-6, or one returns a value where another reports no solution | Phase 4 |
| 8 | Golden fixtures | `pytest tests/golden` | computed ≠ committed expected values | Phase 2 (ledger), Phase 4 (calcs) |
| 9 | Mutation testing | `mutmut` on `ledger`, `analytics`, `policy` | surviving ratio ≥ 10% | Phase 4 (gate), optional earlier; slow, not in pre-commit |
| 10 | Branch coverage | `pytest --cov --cov-branch` | < 95% on `ledger`, `dq`, `analytics`, `policy`, `risk` | Per package from the phase that creates it |
| 11 | Tax-rule check | `scripts/check_tax_rules.py` | any `review_by` < today; any ledger transaction date with no valid rule for its asset class | First phase that adds `config/tax_rules.yaml` |
| 12 | Gate check | `scripts/check_gates.py` | Phase N files exist without `phase-(N-1).approved`; structural rules violated | Phase 1 |
| 13 | Security hygiene | `detect-secrets scan --baseline`, `pip-audit`, licence allow-list, dependency deny-list (ADR-0021: `kiteconnect` and other broker SDKs, `streamlit`, `pymupdf`/`fitz`, `google-api-python-client`, `google-auth-oauthlib`; before Phase 8: ollama/embedding/vector libs; before Phase 6: chart/web libs) | any finding | Phase 1 |

## Import-linter contracts
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
