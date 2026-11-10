# ADR-0014: Encrypted backups with age public-key encryption

Status: Accepted, 2026-11-11

## Decision
- Daily: `pg_dump -Fc` of the database plus a tar of `data/` (imports, raw payloads), encrypted to an age X25519 recipient using `pyrage` (age format). Only the public key is on the machine for the backup job; the private identity is kept by the investor offline/elsewhere and supplied only for restore.
- Archives in `data/backups/` (configurable to another disk), retention configurable (default 30 daily, 12 monthly).
- Weekly restore test: the job writes the dump to a temp file, restores it into scratch DB `arthashreni_restore_test`, compares row counts per table with the source, then encrypts and securely deletes the plaintext. This proves the dump is restorable without the private key being on the machine.
- Full decrypt-and-restore drill (needs the private identity) is a manual step in the Phase 1 and Phase 10 gates.

## Consequences
A stolen backup without the private key is unreadable. Fallback if `pyrage` is unsuitable: `age` CLI binary, same format.
