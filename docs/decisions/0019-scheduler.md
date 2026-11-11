# ADR-0019: Scheduler persistence and missed-run recovery

Status: Accepted, 2026-11-11

## Decision
- APScheduler 3.x `BackgroundScheduler`, timezone `Asia/Kolkata`, `SQLAlchemyJobStore` on the project database, so job definitions and next run times survive restarts.
- Job defaults: `coalesce=True` (several missed runs of the same job collapse into one), `max_instances=1`, `misfire_grace_time` long enough to cover a machine that was off (default 7 days; configurable per job).
- APScheduler's misfire handling alone is not trusted. On start-up the scheduler also reads `ref.job_runs`, works out every `scheduled_for` slot since each job's last successful run, and runs the missing slots in order (daily jobs once per missed day where the job needs it, e.g. ingest for each missed trading day; others once). `UNIQUE(job_name, scheduled_for)` makes reruns idempotent.
- The scheduler runs as a systemd user service with restart on failure. cron is not used (ADR-0021).

## Consequences
Downtime is recovered on the next start and every run, including catch-up, is recorded.

Amended by ADR-0023 (Phase 3): missed daily slots are all recorded, but only the newest slot fetches, because adapters fetch look-back windows.
