# Phase 6 gate report — Minimal read-only UI

Date: 2026-11-21. Branch `phase-6-readonly-ui` (from `main` at `92a237f`, the Phase 5 merge). Nothing is committed yet. The agent stops here and waits for `docs/gates/phase-6.approved`; the agent never creates it.

## 1. Summary
- **`apps/web`:** Next.js 16 + React 19 + TypeScript (strict) + SCSS modules, no Tailwind, no chart library, no chat. Pages: Overview, Portfolio, Transactions & Imports, Data Quality & Sources, Risk Monitor, Monthly Report, plus a sign-in form. Each page is a pure view over API JSON; `page.tsx` only fetches (GET).
- **Every number** is rendered by `<Num>` (API `Measured`) or `<Stated>` (bare recommendation numbers): formatted value, text status badge (OK / Stale / Missing / Estimated / Conflicting / Quarantined) and "as of <date>", with the exact API string kept in `data-value`. Decimals are formatted with string/BigInt arithmetic, never floats. A DEMO/REAL dataset label shows in the header of every page.
- **Two thin read-only endpoints added** (`GET /transactions`, `GET /imports`), because the Transactions and Imports page had no endpoint to render (ADR-0028). This is a deviation from "existing endpoints only"; say so if you want it reversed.
- **Contract test chain:** the real API on the DEMO month produces `apps/web/fixtures/*.json`; Python fails if the API output drifts from them; Vitest renders each page from them.
- **Nothing in the UI writes, approves or executes.** Approval, import commit and quarantine resolution stay in the CLI (Phase 9 adds approval to the UI).

## 2. Gate checklist
| Criterion | Result | Evidence |
| --- | --- | --- |
| Each page matches API values exactly (contract tests) | pass | `tests/contract.test.tsx`: per page, every rendered number is one the API sent; every listed Measured is rendered with the same value and status; status and as-of match the API record; row counts equal API counts; recommendation numbers (lines, envelope, scenarios) match as sent. `tests/test_phase6_contract.py` (8 tests) keeps the fixtures identical to live API output. 38 web tests pass |
| DQ badges visible | pass | Every `<Num>` carries a text badge; test "stale data is labelled stale, not shown as ok" (the DEMO month has stale prices); the Data Quality page shows source freshness badges and a banner when the monthly review is blocked |
| Basic accessibility check passes | pass (automated only) | `vitest-axe` (axe-core) reports no violations on all six pages. Semantic tables with captions and `scope`, skip link, labelled nav with `aria-current`, labelled login form, visible focus. **Not covered by jsdom:** colour contrast, real keyboard and screen-reader use (see section 7) |
| Renders only existing endpoints | pass with one deviation | Existing: `/portfolio`, `/risk`, `/review`, `/sources/health`, `/quarantine`, `/auth/*`, `/health`. Added: `/transactions`, `/imports` (read-only, ADR-0028) |
| Every number shows status and as-of | pass | Test "puts status and as-of next to every number" on all pages |
| No chat interface | pass | No chat code; `tests/readonly.test.ts` also rejects chart, chat and Tailwind dependencies |
| UI is read-only | pass | `tests/readonly.test.ts`: no POST/PUT/PATCH/DELETE in `src` except the login form; Python test: the read endpoints have no write handler |
| Proxy and auth work end to end | pass (partial) | Production build served all six pages (HTTP 200) on 127.0.0.1:3100; `/api/health` returned the API's health through the proxy; `/api/portfolio` without a session returned 401. **Not done:** a browser sign-in and visual check, because no login user's password is available to the agent |

## 3. `make verify` result
Unabridged: `docs/gates/phase-6.verify.log`.
```
ruff check / ruff format / mypy --strict (102 files): ok
no float in money packages: ok          import contracts: 10 kept, 0 broken
gate check / secret scan: ok            tests: 601 passed, 1 skipped (total coverage 97%)
web: lint / typecheck / tests (38 passed) / build: ok
branch coverage >= 95% (ledger, dq, analytics, policy, risk): ok
property tests (1000 examples): 24 passed
golden fixtures, golden values, golden calculations: ok       XIRR oracle: ok
tax-rule check: skipped (no tax rules yet; unchanged since Phase 5)
dependency audit: ok (Python and `npm audit --omit=dev`, 0 vulnerabilities)
mutation survivors < 10% (analytics, ledger, policy): ok
verify: all steps passed
```

## 4. Changes
```
apps/web/{package.json,package-lock.json,next.config.ts,tsconfig.json,vitest.config.ts,eslint.config.mjs,.gitignore}
apps/web/src/{app/*/page.tsx,app/layout.tsx,components/{M,Load,Shell}.*,lib/{api,format,types}.ts,views/*.tsx,styles/globals.scss}
apps/web/tests/{contract.test.tsx,format.test.ts,readonly.test.ts,setup.ts,vitest-axe.d.ts}
apps/web/fixtures/*.json                      (7 API responses, generated)
src/arthashreni/api/ledger.py                 (GET /transactions, GET /imports);  api/app.py (router)
tests/test_phase6_contract.py
scripts/verify.py                             (web lint, typecheck, tests, build, npm audit)
docs: ADR-0028, decisions/README, architecture, runbook
```
- **Migrations:** none. **New endpoints:** `GET /transactions?limit&offset`, `GET /imports` (login required, read-only). **New config:** `ARTHA_API_URL` (proxy target, fixed at `next build`). **Dependencies:** web only (Next, React, Sass; dev: TypeScript, Vitest, jsdom, Testing Library, vitest-axe, ESLint). No Python dependency added.
- Earlier-phase files touched: `api/app.py` (one router) and `scripts/verify.py` (new steps). `.gitignore` at the repo root also shows a `graphify-out/` and `node_modules/` change that this phase did not make; check it before committing.

## 5. Run commands (Linux only, ADR-0018)
```bash
make install && make migrate && make user          # once
uv run arthashreni demo seed && uv run arthashreni demo market && uv run arthashreni demo policy
make run                                            # API on 127.0.0.1:8000
cd apps/web && npm ci
ARTHA_API_URL=http://127.0.0.1:8000 npm run build && ARTHA_API_URL=http://127.0.0.1:8000 npm run start   # http://127.0.0.1:3100
npm test                                            # web contract + accessibility tests
UPDATE_UI_FIXTURES=1 uv run pytest tests/test_phase6_contract.py --no-cov    # after an API change
make verify
```
If port 8000 is taken (it was here, by another project), set `ARTHA_API_PORT` and pass the same port in `ARTHA_API_URL` at build time.

## 6. Decisions (ADR-0028; reversible, none touches Part A)
1. No chart library yet: Phase 6 needs tables, not charts. Choose one by ADR when a page needs it.
2. Same-origin proxy (`/api/*`) instead of CORS; keeps the `SameSite=Strict` cookie.
3. UI port 3100, because 3000 and 8000 were occupied by other local projects.
4. Ledger rows are shown as `Measured` with status `ok` and the trade date as-of; they are recorded facts, not market data.
5. Bare recommendation numbers show only the status the API gives (line `data_freshness`, scenario `status`) plus the review date; the UI invents no status.
- [VERIFY] items: none touched. Licences of direct web dependencies checked in `node_modules` (MIT; TypeScript Apache-2.0).

## 7. Known limitations
- **Accessibility is automated only.** Colour contrast, keyboard order and screen-reader behaviour need a manual pass in a browser (suggest: Tab through each page, and check both light and dark themes). I did not do a visual browser check.
- **No browser sign-in was run** (no password available to the agent); the login form is covered by build and type checks only, and the 401/200 behaviour of the API by existing tests.
- The Monthly Report page renders the recommendation JSON; the Markdown/PDF report from Phase 5 has no endpoint, so it is not shown. The page shows the stored month if one exists, otherwise what the engines would produce now, and says which.
- `/transactions` returns the effective view only (no audit view of superseded rows) and is capped at 1,000 rows per call; the page shows the latest 200 without paging controls.
- Signals missing in Phase 5 (rates, inflation, oil, breadth) show as "no data", not as zero.
- `npm install` printed a notice that the optional `@parcel/watcher` install script was not run (a dev-server file-watching extra); production builds and tests are unaffected.
- The web build needs Node 24 and the proxy target is fixed at build time.

## 8. Proposed next phase (Phase 7 — Research and IPO module)
Fundamentals, fund/ETF look-through overlap from disclosure files, commodity drivers, IPO tracker with RHP data and classifications, official news feeds with de-duplication, claims table. Gate: overlap reconciles on a fixture; duplicate news collapses to one event; IPO eligibility respects the sinking fund and SME exclusion.

**Reminders (investor action):**
- Create `docs/gates/phase-6.approved` only after you have looked at the pages in a browser (`make run`, then the web commands above).
- The Phase 5 reminders still stand: write your IPS, fill `config/costs.toml`, rotate the `artha_owner` password, read the first real import yourself.
