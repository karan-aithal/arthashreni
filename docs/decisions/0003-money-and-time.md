# ADR-0003: Money, quantity and time representation

Status: Accepted, 2026-11-11

## Decision
- Money `NUMERIC(20,4)`; quantities, prices, NAV, FX `NUMERIC(24,8)`; ratios `NUMERIC(12,8)`. Python `decimal.Decimal` with a module-level context (precision 28, `ROUND_HALF_EVEN`). Rounding only at display and at explicit domain points (MF units to 3 decimals when compared to CAS).
- Float is banned in `ledger`, `analytics`, `policy`, `risk` by a ruff/grep check in `make verify` (except inside the XIRR solver, which converts at its boundary and returns `Decimal`; documented exception).
- Timestamps `TIMESTAMPTZ` stored UTC; the source time zone kept in `source_tz`. Business dates are `DATE` in the exchange/AMFI local calendar.

## Consequences
No binary-float drift in money. Slight performance cost, irrelevant at this portfolio size.
