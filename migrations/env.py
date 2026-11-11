"""Alembic environment. Runs as artha_owner; version table lives in schema ref.

Set ARTHA_MIGRATE_URL to migrate a different database (tests, restore checks).
"""

import os

from alembic import context
from sqlalchemy import create_engine, text

from arthashreni.core.config import get_settings

url = os.environ.get("ARTHA_MIGRATE_URL") or get_settings().db_owner_url.get_secret_value()

engine = create_engine(url)
with engine.begin() as conn:
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS ref"))
    context.configure(connection=conn, version_table_schema="ref")
    with context.begin_transaction():
        context.run_migrations()
