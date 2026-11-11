"""Market data, data-quality tables, source registry, job runs (Phase 3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11

Market tables are bitemporal and append-only (ADR-0008): `valid_at` is when the fact was true,
`recorded_at` when we learned it. A restatement or an accepted quarantine item is a new row.
Quarantined rows are stored (status `quarantined`) so they can be reviewed, and `as_of` reads skip them.
`dq_quarantine` and `source_conflicts` are review queues: the app role may update the resolution columns only.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

STATUSES = "ok estimated stale conflicting missing quarantined".split()
APP_ROLE = {"demo": "artha_demo", "real": "artha_real"}


def q(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


QUALITY = f"""quality_status text NOT NULL DEFAULT 'ok' CHECK (quality_status IN ({q(STATUSES)})),
            quality_notes text[] NOT NULL DEFAULT '{{}}',
            recorded_at timestamptz NOT NULL DEFAULT now()"""

APPEND_ONLY = ["raw_payloads", "prices", "fx_rates", "fundamentals"]


def dataset_sql(s: str) -> list[str]:
    r = APP_ROLE[s]
    stmts = [
        f"""CREATE TABLE {s}.raw_payloads (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source_id text NOT NULL REFERENCES ref.data_source_registry(id),
            fetched_at timestamptz NOT NULL DEFAULT now(),
            window_start date,
            window_end date,
            sha256 text NOT NULL,
            file_path text,
            http_status integer,
            schema_ok boolean NOT NULL,
            detail text
        )""",
        f"""CREATE TABLE {s}.prices (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            asset_id bigint NOT NULL REFERENCES ref.assets(id),
            valid_at date NOT NULL,
            close numeric(24, 8) NOT NULL,
            currency text NOT NULL CHECK (length(currency) = 3),
            price_type text NOT NULL CHECK (price_type IN ('CLOSE', 'NAV', 'STATEMENT')),
            split_factor numeric(24, 8),
            source_id text NOT NULL REFERENCES ref.data_source_registry(id),
            raw_payload_id bigint REFERENCES {s}.raw_payloads(id),
            import_batch_id bigint REFERENCES {s}.import_batches(id),
            {QUALITY},
            CHECK (close > 0 OR quality_status = 'quarantined')
        )""",
        f"CREATE INDEX prices_asof ON {s}.prices (asset_id, valid_at, recorded_at)",
        f"""CREATE TABLE {s}.fx_rates (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            pair text NOT NULL DEFAULT 'USDINR',
            rate_type text NOT NULL CHECK (rate_type IN ('SBI_TT_BUY', 'FBIL_REF', 'BROKER_ACTUAL')),
            underlying_series text NOT NULL CHECK (underlying_series IN ('FBIL', 'SBI', 'BROKER')),
            provider text NOT NULL,
            valid_at date NOT NULL,
            observed_at timestamptz,
            rate numeric(24, 8) NOT NULL,
            source_id text NOT NULL REFERENCES ref.data_source_registry(id),
            raw_payload_id bigint REFERENCES {s}.raw_payloads(id),
            {QUALITY},
            CHECK (rate > 0 OR quality_status = 'quarantined')
        )""",
        f"CREATE INDEX fx_rates_asof ON {s}.fx_rates (pair, rate_type, valid_at, recorded_at)",
        f"""CREATE TABLE {s}.fundamentals (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            asset_id bigint NOT NULL REFERENCES ref.assets(id),
            concept text NOT NULL,
            valid_at date NOT NULL,
            fiscal_year integer,
            fiscal_period text,
            form text,
            unit text NOT NULL,
            value numeric(30, 6) NOT NULL,
            filed_at date,
            accession_no text NOT NULL,
            source_id text NOT NULL REFERENCES ref.data_source_registry(id),
            raw_payload_id bigint REFERENCES {s}.raw_payloads(id),
            {QUALITY},
            UNIQUE (asset_id, concept, valid_at, fiscal_period, unit, accession_no)
        )""",
        f"CREATE INDEX fundamentals_asof ON {s}.fundamentals (asset_id, concept, valid_at, recorded_at)",
        f"""CREATE TABLE {s}.dq_quarantine (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            table_name text NOT NULL,
            row_ref text NOT NULL,
            check_name text NOT NULL,
            source_id text,
            detail jsonb NOT NULL DEFAULT '{{}}',
            created_at timestamptz NOT NULL DEFAULT now(),
            resolution text NOT NULL DEFAULT 'PENDING'
                CHECK (resolution IN ('PENDING', 'ACCEPTED', 'CORRECTED', 'REJECTED', 'AUTO_CLEARED')),
            resolved_at timestamptz,
            resolution_note text,
            CHECK ((resolution = 'PENDING') = (resolved_at IS NULL))
        )""",
        f"""CREATE UNIQUE INDEX dq_quarantine_open ON {s}.dq_quarantine (table_name, row_ref, check_name)
            WHERE resolution = 'PENDING'""",
        f"""CREATE TABLE {s}.source_conflicts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            subject text NOT NULL,
            valid_at date NOT NULL,
            observation_ids bigint[] NOT NULL,
            chosen_id bigint NOT NULL,
            rule text NOT NULL,
            diff_pct numeric(12, 6) NOT NULL,
            detected_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (subject, valid_at, chosen_id)
        )""",
    ]
    for t in APPEND_ONLY:
        stmts += [
            f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {s}.{t} "
            "FOR EACH ROW EXECUTE FUNCTION ref.reject_mutation()",
            f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {s}.{t} "
            "FOR EACH STATEMENT EXECUTE FUNCTION ref.reject_mutation()",
        ]
    stmts += [
        f"GRANT SELECT, INSERT ON {s}.raw_payloads, {s}.prices, {s}.fx_rates, {s}.fundamentals, "
        f"{s}.dq_quarantine, {s}.source_conflicts TO {r}",
        f"GRANT UPDATE (resolution, resolved_at, resolution_note) ON {s}.dq_quarantine TO {r}",
    ]
    return stmts


def upgrade() -> None:
    op.execute("ALTER TABLE ref.assets ADD COLUMN cik text")
    op.execute("GRANT UPDATE (cik) ON ref.assets TO artha_demo, artha_real")
    op.execute(
        """CREATE TABLE ref.data_source_registry (
            id text PRIMARY KEY,
            name text NOT NULL,
            provider text NOT NULL,
            underlying_series text,
            data_category text NOT NULL,
            adapter text,
            auth_type text NOT NULL,
            free_tier text,
            rate_limit text,
            min_interval_seconds numeric(10, 3) NOT NULL DEFAULT 0,
            daily_quota integer,
            historical_coverage text,
            expected_delay_days integer NOT NULL DEFAULT 1,
            terms_url text,
            storage_permitted text,
            redistribution_permitted text,
            priority integer NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            last_success_at timestamptz,
            last_failure_at timestamptz,
            notes text
        )"""
    )
    op.execute(
        """CREATE TABLE ref.source_calls (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source_id text NOT NULL REFERENCES ref.data_source_registry(id),
            dataset text NOT NULL CHECK (dataset IN ('demo', 'real')),
            called_at timestamptz NOT NULL DEFAULT now(),
            ok boolean NOT NULL,
            http_status integer,
            detail text
        )"""
    )
    op.execute("CREATE INDEX source_calls_recent ON ref.source_calls (source_id, called_at)")
    op.execute(
        """CREATE TABLE ref.job_runs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            job_name text NOT NULL,
            scheduled_for timestamptz NOT NULL,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            status text NOT NULL CHECK (status IN ('RUNNING', 'OK', 'PARTIAL', 'FAILED')),
            summary jsonb NOT NULL DEFAULT '{}',
            UNIQUE (job_name, scheduled_for)
        )"""
    )
    op.execute(
        """CREATE TABLE ref.apscheduler_jobs (
            id varchar(191) NOT NULL PRIMARY KEY,
            next_run_time double precision,
            job_state bytea NOT NULL
        )"""
    )
    op.execute("CREATE INDEX ix_apscheduler_jobs_next_run_time ON ref.apscheduler_jobs (next_run_time)")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON ref.data_source_registry, ref.job_runs TO artha_demo, artha_real"
    )
    op.execute("GRANT SELECT, INSERT ON ref.source_calls TO artha_demo, artha_real")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ref.apscheduler_jobs TO artha_demo, artha_real")
    for s in ("demo", "real"):
        for stmt in dataset_sql(s):
            op.execute(stmt)


def downgrade() -> None:
    for s in ("demo", "real"):
        for t in APPEND_ONLY:
            op.execute(f"DROP TRIGGER {t}_append_only ON {s}.{t}")
            op.execute(f"DROP TRIGGER {t}_no_truncate ON {s}.{t}")
        for t in ("source_conflicts", "dq_quarantine", "fundamentals", "fx_rates", "prices", "raw_payloads"):
            op.execute(f"DROP TABLE {s}.{t}")
    for t in ("apscheduler_jobs", "job_runs", "source_calls", "data_source_registry"):
        op.execute(f"DROP TABLE ref.{t}")
    op.execute("ALTER TABLE ref.assets DROP COLUMN cik")
