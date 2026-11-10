# ADR-0005: Append-only enforcement in the database

Status: Accepted, 2026-11-11

## Decision
For `transactions`, `transaction_imports_raw`, `corporate_actions`, `reserve_ledger`, `decisions`, `raw_payloads` and all bitemporal market tables:
1. A `BEFORE UPDATE OR DELETE` trigger raises an exception.
2. The application role is granted `SELECT, INSERT` only.
3. Migrations run as a separate owner role.
Corrections are `CORRECTION` rows with `supersedes_id` and `correction_reason`; readers use `transactions_effective`.

## Consequences
History cannot be rewritten by the app even through a bug. Fixing a bad import means corrections, which is the intended audit trail.
