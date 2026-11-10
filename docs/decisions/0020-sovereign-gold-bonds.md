# ADR-0020: Sovereign Gold Bonds as existing holdings only

Status: Accepted, 2026-11-11

## Decision
- SGBs are an existing-holdings asset class (`asset_class = SGB`). No new-issue subscription workflow is built (V11: no issuance since Feb 2024).
- Supported: holdings (from NSDL/CDSL CAS), interest receipts (new transaction type `INTEREST`), maturity date and coupon on the asset row, premature and final redemption (`REDEMPTION`), and the resulting cash flows in returns.
- Valuation: primary is the statement price (CAS market value per unit), handled like other statement prices (age shown, stale flagged). An estimated value from a gold price source is allowed only when such a source is added through an ADR, and is marked `estimated`.
- Tax on SGB interest and redemption is taken only from verified rules in `config/tax_rules.yaml` [VERIFY in the tax phase].

## Consequences
Adds one ledger transaction type (`INTEREST`) to the C1 list.
