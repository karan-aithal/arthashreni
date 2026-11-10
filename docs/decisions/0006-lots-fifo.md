# ADR-0006: Lots are derived; FIFO consumption

Status: Accepted, 2026-11-11

## Decision
`lots` and `lot_consumptions` are rebuilt deterministically from `transactions_effective` and `corporate_actions`; they are caches, never edited by hand. FIFO order is `(acquired_on, transaction_id)`. Corporate actions are applied as explicit events in date order (split/bonus multiply quantity and divide per-unit cost; merger maps to the successor asset). Average cost is computed for display only and labelled so.
- FIFO scope is per asset per account (per folio for mutual funds, per demat/broker account for securities).
- Tax on a disposal is selected from `tax_rules` by the investor's jurisdiction (India, resident), the asset class, the holding period of each consumed lot and the **disposal date**; foreign-source items (US dividend withholding, foreign tax credit) add rules for the source jurisdiction. Each consumed lot is taxed under the rule valid on the disposal date for its own holding period. No matching verified rule means "tax unavailable".

## Consequences
A property test asserts: rebuilt holdings = signed sum of ledger quantities, per asset and account, after any sequence of events.
