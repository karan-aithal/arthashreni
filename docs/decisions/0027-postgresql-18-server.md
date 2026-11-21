# ADR-0027: PostgreSQL 18 on port 5433 is the project server

Status: Accepted, 2026-11-21 (investor instruction). Changes no Part A invariant. Supersedes the server choice in ADR-0001 (PostgreSQL 14.24); the "no features newer than 14 used" rule in ADR-0001 still stands.

## Context
Two clusters run on this machine: 14.25 on 5432 and 18.7 on 5433. Ubuntu's `pg_dump` wrapper picked the newest installed client (19), whose dump sets `transaction_timeout`; the 14 server rejected it on restore and two backup tests failed. PostgreSQL 19 was removed; 14 and 18 remain.

## Decision
- The project database `arthashreni` and the roles `artha_owner`, `artha_demo`, `artha_real` live on the **18 cluster, port 5433**. `.env` points there. `scripts/db_setup.sh` takes the port from `PORT` and passes it to `psql`.
- `backup.py` runs `pg_dump` and `pg_restore` from `/usr/lib/postgresql/18/bin` (override: `ARTHA_PG_CLIENT_BIN`), so the client always matches the server major version.
- The 14 cluster on 5432 stays installed and unused. It held no data when the switch was made. Old settings are kept in the investor's home directory, outside the repository.
- Code still avoids features newer than PostgreSQL 14, so moving back remains possible.

## Consequences
`make verify` passes against 18. A future major upgrade means changing the cluster, `PORT` and the pinned client directory together.
