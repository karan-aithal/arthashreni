# ADR-0028: Read-only web UI (Phase 6)

Status: Accepted, 2026-11-21 (agent default, reversible). Changes no Part A invariant.

## Context
Phase 6 asks for a Next.js, TypeScript, SCSS-modules UI with six pages that render existing endpoints, show status and as-of beside every number, have contract tests against the API, visible data-quality badges and a basic accessibility check. No chat interface.

## Decision
- **Stack:** Next.js 16 (App Router), React 19, TypeScript strict, SCSS modules, no Tailwind. Licences checked in `node_modules`: Next, React, Sass, Vitest, jsdom, vitest-axe, Testing Library, ESLint are MIT; TypeScript is Apache-2.0.
- **No chart library.** Phase 6 lists tables and numbers, not charts. A free chart library with a verified licence is chosen by an ADR when a page needs one.
- **Same-origin proxy.** The browser only talks to the Next server; `/api/*` is rewritten to the FastAPI (`ARTHA_API_URL`, default `http://127.0.0.1:8000`). The `SameSite=Strict` session cookie keeps working and no CORS is opened. Next fixes rewrites at build time, so set `ARTHA_API_URL` for `npm run build` and `npm run start`. The UI listens on `127.0.0.1:3100` (3000 and 8000 are often taken by other local projects).
- **Two thin read-only endpoints added**, `GET /transactions` (effective ledger view) and `GET /imports` (batches with row counts). The page list needs them and none existed. They only read, wrap each amount as a `Measured` (status `ok`, as-of = trade date, source = `txn:<id>`, since a ledger row is a recorded fact), and are covered by the same contract test. Committing or rejecting an import, resolving quarantine and approving a recommendation stay in the CLI (approval UI is Phase 9).
- **Numbers stay strings.** The UI never converts an API decimal to a float. `lib/format.ts` rounds and groups with string and BigInt arithmetic. Every API number renders through `<Num>`, which shows the formatted value, a text status badge (never colour alone) and `as of <date>`, and carries the exact API string in `data-value`. Numbers the recommendation JSON sends bare use `<Stated>` with the status the API does give (line `data_freshness`, scenario `status`) and the review date.
- **Contract tests.** `tests/test_phase6_contract.py` seeds the DEMO dataset, calls the real API and compares each response with `apps/web/fixtures/*.json` (wall-clock stamps masked; regenerate with `UPDATE_UI_FIXTURES=1`). Vitest renders every page from those fixtures and checks that: no number appears that the API did not send; every listed API number appears with the same status and as-of; row counts match; stale data shows as Stale. A second test fails on any non-GET request outside the login form and on any chart, chat or Tailwind dependency.
- **Accessibility check.** `vitest-axe` (axe-core) runs on every page in `make verify`. Semantic tables with captions and header scopes, a skip link, labelled navigation, `aria-current`, visible focus.

## Consequences
`make verify` gains web lint, typecheck, tests, build and `npm audit --omit=dev`. Node 24 and npm are now build requirements. jsdom cannot judge colour contrast, focus order or screen-reader behaviour, so the manual keyboard and contrast pass remains with the investor (listed in the Phase 6 report).
