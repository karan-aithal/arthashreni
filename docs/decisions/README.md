# Architecture Decision Records

Format: Context, Decision, Consequences, Status. Status is `Accepted` (reversible default chosen by the agent, per Part I), `Proposed` (needs investor approval) or `Superseded by ADR-NNNN`.
Changing a Part A invariant requires a `Proposed` ADR and investor approval before any code changes.

| ADR | Title | Status |
| --- | --- | --- |
| 0001 | Stack and repository layout | Accepted |
| 0002 | uv for environments and lockfile | Accepted |
| 0003 | Money, quantity and time representation | Accepted |
| 0004 | DEMO/REAL isolation by PostgreSQL schema | Accepted |
| 0005 | Append-only enforcement in the database | Accepted |
| 0006 | Lots are derived; FIFO consumption | Accepted |
| 0007 | `Measured` type and quality ordering | Accepted |
| 0008 | Bitemporal market data and `as_of` reads | Accepted |
| 0009 | Budget envelope enforced by database constraints | Accepted |
| 0010 | Deterministic confidence formula | Accepted |
| 0011 | No execution capability, enforced structurally | Accepted |
| 0012 | Local single-user authentication | Accepted |
| 0013 | `make verify` implemented as a Python script | Accepted |
| 0014 | Encrypted backups with age public-key encryption | Accepted |
| 0015 | Initial data-source selections | Accepted |
| 0016 | FX rate types and where each is used | Accepted |
| 0017 | Leftover-cash default pending IPS | Accepted (assumption) |
| 0018 | Linux-only target; what git tracks | Accepted |
| 0019 | Scheduler persistence and missed-run recovery | Accepted |
| 0020 | Sovereign Gold Bonds as existing holdings only | Accepted |
| 0021 | Excluded technologies and data scope | Accepted |
| 0022 | Ledger conventions (Phase 2) | Accepted |
