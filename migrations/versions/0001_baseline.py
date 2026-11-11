"""Baseline: dataset schemas, role grants, local auth tables.

Revision ID: 0001
Revises:
Create Date: 2026-11-11
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADR-0004: ref (shared, non-personal), demo and real (one dataset each).
    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    op.execute("CREATE SCHEMA IF NOT EXISTS demo")
    op.execute("CREATE SCHEMA IF NOT EXISTS real")
    op.execute("GRANT USAGE ON SCHEMA ref TO artha_demo, artha_real")
    op.execute("GRANT USAGE ON SCHEMA demo TO artha_demo")
    op.execute("GRANT USAGE ON SCHEMA real TO artha_real")
    op.execute("REVOKE ALL ON SCHEMA demo FROM artha_real")
    op.execute("REVOKE ALL ON SCHEMA real FROM artha_demo")

    op.execute(
        """
        CREATE TABLE ref.local_user (
            id smallint PRIMARY KEY,
            username text NOT NULL,
            password_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT single_user CHECK (id = 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ref.auth_sessions (
            token_hash text PRIMARY KEY,
            csrf_token text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL
        )
        """
    )
    # App roles read the user; only the CLI (owner) creates or resets it.
    op.execute("GRANT SELECT ON ref.local_user TO artha_demo, artha_real")
    op.execute("GRANT SELECT, INSERT, DELETE ON ref.auth_sessions TO artha_demo, artha_real")


def downgrade() -> None:
    op.execute("DROP TABLE ref.auth_sessions")
    op.execute("DROP TABLE ref.local_user")
    op.execute("DROP SCHEMA real")
    op.execute("DROP SCHEMA demo")
