"""Research tables (Phase 7): fund holdings, news events, IPO checklist and classifications, claims.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-22

Fund holdings are bitemporal (`valid_at` = disclosure date, `recorded_at` = when we learned it) and never
updated. News events are de-duplicated by `dedupe_key`; only the sighting counters may change. IPO checklist
facts and classifications are append-only, so the reason an IPO was allowed is kept (ADR-0030).
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = {"demo": "artha_demo", "real": "artha_real"}
STATUSES = "ok estimated stale conflicting missing quarantined".split()
FACTS = (
    "min_lot_value issue_size objects_of_issue promoter_holding financials_3y risk_factors_reviewed".split()
)
EVIDENCE = "NEWS IPO_FACT FUNDAMENTAL HOLDINGS".split()
APPEND_ONLY = ["fund_holdings", "ipo_research", "ipo_classifications", "research_claims"]


def q(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


def dataset_sql(s: str) -> list[str]:
    r = APP_ROLE[s]
    stmts = [
        f"""CREATE TABLE {s}.fund_holdings (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            fund_asset_id bigint NOT NULL REFERENCES ref.assets(id),
            valid_at date NOT NULL,
            underlying_key text NOT NULL,
            underlying_name text NOT NULL,
            weight numeric(14,10) NOT NULL CHECK (weight >= 0 AND weight <= 1),
            source_id text NOT NULL,
            file_sha256 text NOT NULL,
            quality_status text NOT NULL DEFAULT 'ok' CHECK (quality_status IN ({q(STATUSES)})),
            recorded_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (fund_asset_id, valid_at, underlying_key, file_sha256)
        )""",
        f"CREATE INDEX fund_holdings_asof ON {s}.fund_holdings (fund_asset_id, valid_at, recorded_at)",
        f"""CREATE TABLE {s}.news_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            dedupe_key text NOT NULL UNIQUE,
            title text NOT NULL,
            summary text NOT NULL,
            url text NOT NULL,
            published_at timestamptz NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            seen_count integer NOT NULL DEFAULT 1 CHECK (seen_count >= 1),
            sources text[] NOT NULL
        )""",
        f"""CREATE TABLE {s}.ipo_research (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ipo_ref text NOT NULL REFERENCES {s}.ipo_applications(ipo_ref),
            fact text NOT NULL CHECK (fact IN ({q(FACTS)})),
            value text NOT NULL CHECK (length(value) > 0),
            source_doc text NOT NULL CHECK (length(source_doc) > 0),
            recorded_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE {s}.ipo_classifications (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            ipo_ref text NOT NULL REFERENCES {s}.ipo_applications(ipo_ref),
            research_class text NOT NULL CHECK (research_class IN ('ALLOWED', 'NOT_ALLOWED')),
            reason text NOT NULL CHECK (length(reason) > 0),
            decided_at timestamptz NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE {s}.research_claims (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            subject text NOT NULL CHECK (length(subject) > 0),
            claim text NOT NULL CHECK (length(claim) > 0),
            evidence_kind text NOT NULL CHECK (evidence_kind IN ({q(EVIDENCE)})),
            evidence_id bigint NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
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
        f"GRANT SELECT, INSERT ON {', '.join(f'{s}.{t}' for t in APPEND_ONLY)}, {s}.news_events TO {r}",
        f"GRANT UPDATE (last_seen_at, seen_count, sources) ON {s}.news_events TO {r}",
    ]
    return stmts


def upgrade() -> None:
    for s in ("demo", "real"):
        for stmt in dataset_sql(s):
            op.execute(stmt)


def downgrade() -> None:
    for s in ("demo", "real"):
        for t in ("research_claims", "ipo_classifications", "ipo_research", "news_events", "fund_holdings"):
            op.execute(f"DROP TABLE {s}.{t}")
