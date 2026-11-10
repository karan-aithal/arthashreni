# ADR-0011: No execution capability, enforced structurally

Status: Accepted, 2026-11-11

## Decision
- No module, dependency or endpoint can place, modify or cancel an order, SIP or mandate.
- `import-linter` forbidden contract: no module may import anything matching `*.execution`, `*.broker*`, `*.orders*`; `scripts/check_gates.py` also fails if such paths exist.
- Broker SDKs (e.g. `kiteconnect`) are on a dependency deny-list checked in `make verify`.
- Approval endpoints write to `decisions` only.

## Consequences
Execution can only be added by an ADR that changes invariant A3.2 with investor approval.
