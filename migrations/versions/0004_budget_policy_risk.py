"""Budget envelope, SIP mandates, reserves, IPS, personal context, IPO, risk, recommendations (Phase 5).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-12

Over-allocation is a database error (ADR-0009, ADR-0025):
- budget_periods CHECK: committed + new + reserve transfers + unallocated = opening, each >= 0.
- envelope_lines are append-only; a deferred constraint trigger checks at commit that line sums by kind equal
  the period columns. A period goes OPEN -> ALLOCATED once and is then frozen.
- recommendation_lines.envelope_line_id is UNIQUE: one rupee, one use.
- reserve_ledger rejects any entry that would make a balance (or blocked IPO money) negative.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = {"demo": "artha_demo", "real": "artha_real"}
ACTIONS = "continue_sip accumulate reduce review hold avoid hold_cash no_action".split()
STATUSES = "ok estimated stale conflicting missing quarantined".split()


def q(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


FUNCTIONS = [
    """CREATE FUNCTION ref.reserve_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE avail numeric; blocked numeric;
    BEGIN
        PERFORM pg_advisory_xact_lock(hashtext(TG_TABLE_SCHEMA || ':reserve:' || NEW.reserve_id));
        EXECUTE format(
            'SELECT coalesce(sum(CASE WHEN kind IN (''TRANSFER_IN'',''DIVIDEND_IN'',''SALE_PROCEEDS_IN'',''IPO_RELEASE'')
                                      THEN amount_inr WHEN kind IN (''USE'',''IPO_BLOCK'') THEN -amount_inr ELSE 0 END), 0),
                    coalesce(sum(CASE WHEN kind = ''IPO_BLOCK'' THEN amount_inr
                                      WHEN kind IN (''IPO_RELEASE'',''IPO_ALLOT'') THEN -amount_inr ELSE 0 END), 0)
             FROM %I.reserve_ledger WHERE reserve_id = $1', TG_TABLE_SCHEMA) INTO avail, blocked USING NEW.reserve_id;
        IF NEW.kind IN ('TRANSFER_IN','DIVIDEND_IN','SALE_PROCEEDS_IN','IPO_RELEASE') THEN avail := avail + NEW.amount_inr;
        ELSIF NEW.kind IN ('USE','IPO_BLOCK') THEN avail := avail - NEW.amount_inr; END IF;
        IF NEW.kind = 'IPO_BLOCK' THEN blocked := blocked + NEW.amount_inr;
        ELSIF NEW.kind IN ('IPO_RELEASE','IPO_ALLOT') THEN blocked := blocked - NEW.amount_inr; END IF;
        IF avail < 0 THEN RAISE EXCEPTION 'reserve % would go negative (%)', NEW.reserve_id, avail; END IF;
        IF blocked < 0 THEN RAISE EXCEPTION 'reserve % would release more IPO money than is blocked', NEW.reserve_id; END IF;
        RETURN NEW;
    END $$""",
    """CREATE FUNCTION ref.period_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'budget periods are never deleted'; END IF;
        IF OLD.status = 'ALLOCATED' THEN RAISE EXCEPTION 'period % is already allocated', OLD.period; END IF;
        IF NEW.period <> OLD.period OR NEW.opening_budget_inr <> OLD.opening_budget_inr THEN
            RAISE EXCEPTION 'period and opening budget cannot change';
        END IF;
        RETURN NEW;
    END $$""",
    """CREATE FUNCTION ref.line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE st text;
    BEGIN
        EXECUTE format('SELECT status FROM %I.budget_periods WHERE period = $1', TG_TABLE_SCHEMA) INTO st USING NEW.period;
        IF st = 'ALLOCATED' THEN RAISE EXCEPTION 'period % is already allocated', NEW.period; END IF;
        RETURN NEW;
    END $$""",
    """CREATE FUNCTION ref.envelope_sums() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE bad boolean;
    BEGIN
        EXECUTE format(
            'SELECT NOT (b.committed_sip_inr = coalesce((SELECT sum(amount_inr) FROM %1$I.envelope_lines WHERE period = b.period AND kind = ''SIP''), 0)
                     AND b.new_allocations_inr = coalesce((SELECT sum(amount_inr) FROM %1$I.envelope_lines WHERE period = b.period AND kind = ''NEW_ALLOCATION''), 0)
                     AND b.reserve_transfers_inr = coalesce((SELECT sum(amount_inr) FROM %1$I.envelope_lines WHERE period = b.period AND kind = ''RESERVE_TRANSFER''), 0))
             FROM %1$I.budget_periods b WHERE b.period = $1', TG_TABLE_SCHEMA) INTO bad USING NEW.period;
        IF bad THEN RAISE EXCEPTION 'envelope lines of period % do not add up to the period totals', NEW.period; END IF;
        RETURN NULL;
    END $$""",
    """CREATE FUNCTION ref.line_amount_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE amt numeric;
    BEGIN
        IF NEW.envelope_line_id IS NOT NULL THEN
            EXECUTE format('SELECT amount_inr FROM %I.envelope_lines WHERE id = $1', TG_TABLE_SCHEMA) INTO amt USING NEW.envelope_line_id;
            IF amt <> NEW.amount_inr THEN RAISE EXCEPTION 'recommendation line amount differs from its envelope line'; END IF;
        END IF;
        RETURN NEW;
    END $$""",
    """CREATE FUNCTION ref.ips_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'IPS versions are never deleted'; END IF;
        IF OLD.status <> 'DRAFT' AND (NEW.body <> OLD.body OR NEW.version <> OLD.version) THEN
            RAISE EXCEPTION 'an approved IPS version cannot be edited; write a new version';
        END IF;
        IF NOT ((OLD.status = NEW.status) OR (OLD.status = 'DRAFT' AND NEW.status = 'APPROVED')
                OR (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED')) THEN
            RAISE EXCEPTION 'IPS status cannot go from % to %', OLD.status, NEW.status;
        END IF;
        RETURN NEW;
    END $$""",
]

APPEND_ONLY = [
    "budget_settings",
    "sip_mandates",
    "envelope_lines",
    "reserve_ledger",
    "decisions",
    "recommendations",
    "recommendation_lines",
    "personal_context",
    "risk_signals",
    "risk_levels",
]


def dataset_sql(s: str) -> list[str]:
    r = APP_ROLE[s]
    stmts = [
        f"""CREATE TABLE {s}.budget_settings (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            monthly_budget_inr numeric(20,2) NOT NULL CHECK (monthly_budget_inr > 0),
            effective_from date NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE {s}.sip_mandates (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            supersedes_id bigint REFERENCES {s}.sip_mandates(id),
            asset_id bigint NOT NULL REFERENCES ref.assets(id),
            account_id bigint NOT NULL REFERENCES {s}.accounts(id),
            amount_inr numeric(20,2) NOT NULL CHECK (amount_inr > 0),
            day_of_month smallint NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
            platform text NOT NULL,
            start_date date NOT NULL,
            end_date date,
            status text NOT NULL CHECK (status IN ('ACTIVE', 'PAUSED', 'ENDED')),
            recorded_at timestamptz NOT NULL DEFAULT now(),
            CHECK (end_date IS NULL OR end_date >= start_date)
        )""",
        f"CREATE UNIQUE INDEX sip_mandates_one_successor ON {s}.sip_mandates (supersedes_id) WHERE supersedes_id IS NOT NULL",
        f"""CREATE TABLE {s}.reserves (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name text NOT NULL UNIQUE,
            kind text NOT NULL CHECK (kind IN ('CASH_RESERVE', 'IPO_SINKING_FUND')),
            target_inr numeric(20,2) CHECK (target_inr IS NULL OR target_inr > 0)
        )""",
        f"""CREATE TABLE {s}.budget_periods (
            period date PRIMARY KEY CHECK (extract(day FROM period) = 1),
            status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'ALLOCATED')),
            budget_setting_id bigint NOT NULL REFERENCES {s}.budget_settings(id),
            opening_budget_inr numeric(20,2) NOT NULL CHECK (opening_budget_inr >= 0),
            committed_sip_inr numeric(20,2) NOT NULL CHECK (committed_sip_inr >= 0),
            new_allocations_inr numeric(20,2) NOT NULL DEFAULT 0 CHECK (new_allocations_inr >= 0),
            reserve_transfers_inr numeric(20,2) NOT NULL DEFAULT 0 CHECK (reserve_transfers_inr >= 0),
            unallocated_inr numeric(20,2) NOT NULL CHECK (unallocated_inr >= 0),
            opened_at timestamptz NOT NULL DEFAULT now(),
            allocated_at timestamptz,
            CONSTRAINT envelope_reconciles CHECK (
                committed_sip_inr + new_allocations_inr + reserve_transfers_inr + unallocated_inr = opening_budget_inr),
            CHECK ((status = 'ALLOCATED') = (allocated_at IS NOT NULL))
        )""",
        f"""CREATE TABLE {s}.envelope_lines (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            period date NOT NULL REFERENCES {s}.budget_periods(period),
            kind text NOT NULL CHECK (kind IN ('SIP', 'NEW_ALLOCATION', 'RESERVE_TRANSFER', 'RESERVE_USE')),
            amount_inr numeric(20,2) NOT NULL CHECK (amount_inr > 0),
            asset_id bigint REFERENCES ref.assets(id),
            reserve_id bigint REFERENCES {s}.reserves(id),
            sip_mandate_id bigint REFERENCES {s}.sip_mandates(id),
            CHECK ((kind IN ('SIP', 'NEW_ALLOCATION') AND asset_id IS NOT NULL AND reserve_id IS NULL)
                OR (kind = 'RESERVE_TRANSFER' AND reserve_id IS NOT NULL AND asset_id IS NULL)
                OR (kind = 'RESERVE_USE' AND reserve_id IS NOT NULL AND asset_id IS NOT NULL))
        )""",
        f"CREATE INDEX envelope_lines_period ON {s}.envelope_lines (period)",
        f"""CREATE TABLE {s}.ips_versions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            version integer NOT NULL UNIQUE,
            status text NOT NULL CHECK (status IN ('DRAFT', 'APPROVED', 'SUPERSEDED')),
            body jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            approved_at timestamptz,
            approved_note text,
            CHECK ((status = 'DRAFT') = (approved_at IS NULL) OR status = 'SUPERSEDED')
        )""",
        f"CREATE UNIQUE INDEX ips_one_approved ON {s}.ips_versions ((true)) WHERE status = 'APPROVED'",
        f"""CREATE TABLE {s}.recommendations (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            period date NOT NULL UNIQUE REFERENCES {s}.budget_periods(period),
            schema_version text NOT NULL,
            payload jsonb NOT NULL,
            confidence text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
            risk_level text NOT NULL,
            ips_version_id bigint REFERENCES {s}.ips_versions(id),
            human_approval_required boolean NOT NULL CHECK (human_approval_required),
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE {s}.recommendation_lines (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            recommendation_id bigint NOT NULL REFERENCES {s}.recommendations(id),
            envelope_line_id bigint UNIQUE REFERENCES {s}.envelope_lines(id),
            action text NOT NULL CHECK (action IN ({q(ACTIONS)})),
            amount_inr numeric(20,2) NOT NULL CHECK (amount_inr >= 0),
            asset_id bigint REFERENCES ref.assets(id),
            detail jsonb NOT NULL DEFAULT '{{}}',
            CHECK (amount_inr = 0 OR envelope_line_id IS NOT NULL)
        )""",
        f"""CREATE TABLE {s}.reserve_ledger (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            reserve_id bigint NOT NULL REFERENCES {s}.reserves(id),
            entry_date date NOT NULL,
            kind text NOT NULL CHECK (kind IN ('TRANSFER_IN', 'USE', 'DIVIDEND_IN', 'SALE_PROCEEDS_IN',
                                               'IPO_BLOCK', 'IPO_RELEASE', 'IPO_ALLOT')),
            amount_inr numeric(20,2) NOT NULL CHECK (amount_inr > 0),
            approved_line_id bigint REFERENCES {s}.recommendation_lines(id),
            sweep_period date,
            note text,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            CHECK (kind <> 'USE' OR approved_line_id IS NOT NULL)
        )""",
        f"""CREATE UNIQUE INDEX reserve_one_sweep ON {s}.reserve_ledger (reserve_id, sweep_period)
            WHERE sweep_period IS NOT NULL""",
        f"""CREATE TABLE {s}.decisions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            recommendation_id bigint NOT NULL REFERENCES {s}.recommendations(id),
            line_id bigint REFERENCES {s}.recommendation_lines(id),
            decision text NOT NULL CHECK (decision IN ('APPROVE', 'REJECT', 'MODIFY', 'DEFER')),
            reason text NOT NULL CHECK (length(btrim(reason)) > 0),
            decided_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE {s}.personal_context (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            field text NOT NULL,
            value jsonb NOT NULL,
            certainty text NOT NULL CHECK (certainty IN ('KNOWN', 'ASSUMED', 'UNKNOWN')),
            recorded_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE {s}.ipo_applications (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ipo_ref text NOT NULL UNIQUE,
            board text NOT NULL CHECK (board IN ('MAINBOARD', 'SME')),
            rhp_document text,
            min_lot_value_inr numeric(20,2) CHECK (min_lot_value_inr > 0),
            research_class text NOT NULL DEFAULT 'UNRATED' CHECK (research_class IN ('UNRATED', 'ALLOWED', 'NOT_ALLOWED')),
            applied_on date,
            status text NOT NULL CHECK (status IN ('PLANNED', 'EXCLUDED', 'APPLIED', 'ALLOTTED', 'PARTIAL', 'REFUNDED', 'NOT_ALLOTTED')),
            CHECK (board = 'MAINBOARD' OR status = 'EXCLUDED'),
            CHECK (status IN ('PLANNED', 'EXCLUDED') OR min_lot_value_inr IS NOT NULL)
        )""",
        f"""CREATE TABLE {s}.risk_signals (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            as_of date NOT NULL,
            signal_id text NOT NULL,
            scope text NOT NULL CHECK (scope IN ('MARKET', 'PORTFOLIO', 'ASSET')),
            asset_id bigint REFERENCES ref.assets(id),
            value numeric(24,8),
            level text CHECK (level IN ('NORMAL', 'CAUTION', 'HIGH_RISK', 'SEVERE_STRESS')),
            status text NOT NULL CHECK (status IN ({q(STATUSES)})),
            threshold_ref text,
            notes text[] NOT NULL DEFAULT '{{}}',
            recorded_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"CREATE INDEX risk_signals_day ON {s}.risk_signals (as_of, signal_id)",
        f"""CREATE TABLE {s}.risk_levels (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            as_of date NOT NULL,
            level text NOT NULL CHECK (level IN ('NORMAL', 'CAUTION', 'HIGH_RISK', 'SEVERE_STRESS')),
            drivers jsonb NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE {s}.alerts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            dedupe_key text NOT NULL UNIQUE,
            first_raised_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            body jsonb NOT NULL,
            state text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'ACKNOWLEDGED', 'CLOSED'))
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
        f"CREATE TRIGGER period_guard BEFORE UPDATE OR DELETE ON {s}.budget_periods "
        "FOR EACH ROW EXECUTE FUNCTION ref.period_guard()",
        f"CREATE TRIGGER line_guard BEFORE INSERT ON {s}.envelope_lines "
        "FOR EACH ROW EXECUTE FUNCTION ref.line_guard()",
        f"CREATE CONSTRAINT TRIGGER lines_sum_lines AFTER INSERT ON {s}.envelope_lines "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ref.envelope_sums()",
        f"CREATE CONSTRAINT TRIGGER lines_sum_period AFTER INSERT OR UPDATE ON {s}.budget_periods "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ref.envelope_sums()",
        f"CREATE TRIGGER reserve_guard BEFORE INSERT ON {s}.reserve_ledger "
        "FOR EACH ROW EXECUTE FUNCTION ref.reserve_guard()",
        f"CREATE TRIGGER line_amount_guard BEFORE INSERT ON {s}.recommendation_lines "
        "FOR EACH ROW EXECUTE FUNCTION ref.line_amount_guard()",
        f"CREATE TRIGGER ips_guard BEFORE UPDATE OR DELETE ON {s}.ips_versions "
        "FOR EACH ROW EXECUTE FUNCTION ref.ips_guard()",
        f"GRANT SELECT, INSERT ON {', '.join(f'{s}.{t}' for t in APPEND_ONLY)} TO {r}",
        f"GRANT SELECT, INSERT ON {s}.reserves, {s}.ips_versions, {s}.ipo_applications, {s}.alerts TO {r}",
        f"GRANT SELECT, INSERT, UPDATE ON {s}.budget_periods TO {r}",
        f"GRANT UPDATE (target_inr) ON {s}.reserves TO {r}",
        f"GRANT UPDATE (status, approved_at, approved_note) ON {s}.ips_versions TO {r}",
        f"GRANT UPDATE (status, applied_on, min_lot_value_inr, research_class) ON {s}.ipo_applications TO {r}",
        f"GRANT UPDATE (last_seen_at, body, state) ON {s}.alerts TO {r}",
    ]
    return stmts


def upgrade() -> None:
    for fn in FUNCTIONS:
        op.execute(fn)
    for s in ("demo", "real"):
        for stmt in dataset_sql(s):
            op.execute(stmt)


def downgrade() -> None:
    for s in ("demo", "real"):
        for t in (
            "alerts",
            "risk_levels",
            "risk_signals",
            "ipo_applications",
            "personal_context",
            "decisions",
            "reserve_ledger",
            "recommendation_lines",
            "recommendations",
            "ips_versions",
            "envelope_lines",
            "budget_periods",
            "reserves",
            "sip_mandates",
            "budget_settings",
        ):
            op.execute(f"DROP TABLE {s}.{t}")
    for fn in (
        "reserve_guard",
        "period_guard",
        "line_guard",
        "envelope_sums",
        "line_amount_guard",
        "ips_guard",
    ):
        op.execute(f"DROP FUNCTION ref.{fn}()")
