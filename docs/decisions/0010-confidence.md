# ADR-0010: Deterministic confidence formula

Status: Accepted, 2026-11-11 (formula from Part E5; thresholds configurable in `config/confidence.yaml`)

## Decision
For a set of inputs weighted by portfolio value (INR, at FBIL reference rate):
- `coverage` = value share whose inputs are `ok`.
- `freshness` = value share whose inputs are within their source's `expected_delay`.
- `agreement` = share of cross-checked values without `conflicting` status (if nothing could be cross-checked, agreement = 0 and the reason is recorded; it does not default to 1).
- High if all three ≥ 0.95; Medium if all three ≥ 0.80; else Low. Any `quarantined` input forces Low.

## Consequences
Confidence is reproducible and explainable. The "no cross-check means 0" choice makes early months mostly Medium/Low, which is the honest result.
