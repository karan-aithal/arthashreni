# ADR-0009: Budget envelope enforced by database constraints

Status: Accepted, 2026-11-11

## Decision
- `budget_periods` CHECK: `committed + new + reserve_transfers + unallocated = opening`; each component `>= 0`.
- `envelope_lines` with a deferred constraint trigger: per period, line sums by kind equal the period columns at commit.
- `recommendation_lines.envelope_line_id` is `UNIQUE`: one rupee, one use.
- `reserve_ledger` constraint trigger rejects any insert that would make a reserve balance negative.
- **Concurrency:** every allocation runs in one transaction that first takes `SELECT ... FOR UPDATE` on that month's `budget_periods` row, so two allocations for one month run one after the other. Constraint triggers are `DEFERRABLE INITIALLY DEFERRED` and check sums at commit; the unique index rejects a second claim on any envelope line. A property/integration test runs two concurrent allocations for the same month and asserts one waits or fails and the totals still reconcile.
- The same rules exist as Pydantic validators so invalid objects fail before reaching the DB.
- Opening budget comes from `budget_settings` effective on the period date; leftovers never raise it (ADR-0017).

## Consequences
Over-allocation is a database error, not a warning. Hypothesis tests drive random inputs through both layers.
