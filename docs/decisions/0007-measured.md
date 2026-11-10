# ADR-0007: `Measured` type and quality ordering

Status: Accepted, 2026-11-11

## Decision
- `QualityStatus` severity order (best to worst): `ok < estimated < stale < conflicting < missing < quarantined`. Combining takes the worst.
- `Measured[T]` exactly as in Part E2, frozen dataclass. Helpers `m_add`, `m_sub`, `m_mul`, `m_div`, `m_sum` propagate worst status, oldest `as_of`, union of `source_ids` and concatenated notes. Division by zero returns `missing` with a note, never raises into a report.
- Public functions in `analytics`, `risk`, `policy` accept and return `Measured`; `mypy --strict` enforces it.

## Rationale for the order
`estimated` is a value we chose to compute knowingly; `stale` is a value that may be wrong; `conflicting` means sources disagree; `missing` has no value; `quarantined` failed a check and must not be used.
