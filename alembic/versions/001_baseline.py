"""Baseline migration for Supabase schema 001 + 002.

Revision ID: 001_baseline
Revises: None
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from pathlib import Path
from typing import Sequence, Union

revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The canonical base schema lives in the Supabase SQL migrations. They are
# idempotent (CREATE TABLE/INDEX IF NOT EXISTS, CREATE OR REPLACE, DO-blocks
# guarding ADD COLUMN), so applying them here is a no-op on an existing
# Supabase database but creates the base tables on a fresh database (e.g. the
# CI alembic-roundtrip / integration Postgres, which would otherwise fail at
# 002_hnsw_index with 'relation "semantic_memory" does not exist' and at the
# engrams insert with 'relation "engrams" does not exist').
#
# Order matters: 001 creates the memory tables + app_id columns; 002 adds the
# auth tables (users, api_keys) and engrams, and no-ops the app_id ADDs that
# 001 already made.
_SUPABASE_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_BASE_SCHEMA_SQL = (
    _SUPABASE_DIR / "001_initial_schema.sql",
    _SUPABASE_DIR / "002_day2_auth_and_engrams.sql",
)


def upgrade() -> None:
    """
    Create the base schema from the idempotent Supabase SQL migrations.

    On Supabase the tables already exist, so every statement is a no-op
    (IF NOT EXISTS / CREATE OR REPLACE / guarded DO-blocks). On a clean
    database the base tables are created so the rest of the Alembic chain
    (and the integration tests) can run.
    """
    # Ensure extensions exist as a safety measure (also created by the SQL).
    op.execute("CREATE EXTENSION IF NOT EXISTS \"vector\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"pg_trgm\"")

    # Apply each idempotent base schema file if present. We guard on existence
    # so a packaging that omits the supabase/ tree (it is not part of the
    # runtime image) still upgrades cleanly against an existing database.
    for sql_path in _BASE_SCHEMA_SQL:
        if sql_path.is_file():
            op.execute(sql_path.read_text(encoding="utf-8"))


def downgrade() -> None:
    """Leave destructive base-schema teardown as a manual operation."""
    pass


def downgrade() -> None:
    """Leave destructive base-schema teardown as a manual operation."""
    pass
