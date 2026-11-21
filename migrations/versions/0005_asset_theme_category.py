"""Asset theme and mutual-fund category (Phase 5 follow-up, closes V32).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-22

`fund_category` (EQUITY, HYBRID, DEBT) says whether a mutual fund is equity-oriented; NULL means unknown and is
treated as equity (the conservative reading). `theme` lets the IPS theme limit be enforced. Both are set by the
investor, never guessed. Assets stay insert-only except these two columns, like `cik` (ADR-0021).
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ref.assets ADD COLUMN theme text")
    op.execute(
        "ALTER TABLE ref.assets ADD COLUMN fund_category text "
        "CONSTRAINT fund_category_values CHECK (fund_category IN ('EQUITY', 'HYBRID', 'DEBT'))"
    )
    op.execute(
        "ALTER TABLE ref.assets ADD CONSTRAINT fund_category_only_for_mf "
        "CHECK (fund_category IS NULL OR asset_class = 'MF')"
    )
    op.execute("GRANT UPDATE (theme, fund_category) ON ref.assets TO artha_demo, artha_real")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (theme, fund_category) ON ref.assets FROM artha_demo, artha_real")
    op.execute("ALTER TABLE ref.assets DROP CONSTRAINT fund_category_only_for_mf")
    op.execute("ALTER TABLE ref.assets DROP COLUMN fund_category")
    op.execute("ALTER TABLE ref.assets DROP COLUMN theme")
