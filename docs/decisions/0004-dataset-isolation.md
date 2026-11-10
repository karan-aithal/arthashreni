# ADR-0004: DEMO/REAL isolation by PostgreSQL schema

Status: Accepted, 2026-11-11

## Context
Invariant A3.8: demo data must be marked DEMO, stored separately and never mix with real holdings.

## Decision
- Schemas `ref` (shared non-personal reference data), `demo`, `real`. `demo` and `real` contain identical table sets (created by the same migration, parameterised by schema).
- Each DB session is bound to one dataset by setting `search_path` to `<dataset>, ref` at checkout. Selected by config `ARTHA_DATASET=demo|real`; default `demo`.
- Separate DB roles: `artha_demo` can only access `demo`+`ref`; `artha_real` only `real`+`ref`. Cross-schema joins are therefore impossible at the privilege level, not just by convention.
- DEMO market data comes only from committed fixtures, never from live fetches, so DEMO results are reproducible.
- Every API payload includes `dataset`; reports print "DEMO" on every page.
- Writing to `real` is refused by application code until `docs/gates/phase-4.approved` exists.

## Consequences
Doubled table set, trivial cost. Mixing is blocked by the database itself.
