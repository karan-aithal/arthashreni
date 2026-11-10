# ADR-0008: Bitemporal market data and `as_of` reads

Status: Accepted, 2026-11-11

## Decision
Market data, fundamentals, FX and macro rows carry `valid_at` and `recorded_at` and are never updated. Read functions in `arthashreni.db.as_of`: `price_as_of(asset, valid_at, known_at)`, `fx_as_of(pair, rate_type, valid_at, known_at)`, etc. They return the latest row with `valid_at <= :valid_at AND recorded_at <= :known_at`, excluding quarantined rows unless asked. `backtest` and `research` may import only `arthashreni.db.as_of`, enforced by import-linter.

## Consequences
Backtests see only what was known at the time. Storage grows slowly; the portfolio is small.
