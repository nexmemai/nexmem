"""Baseline migration for Supabase schema 001 + 002.

Revision ID: 001_baseline
Revises: None
Create Date: 2026-04-27
"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the base schema using Alembic migrations only."""
    # Note: supabase/migrations/*.sql are reference-only.
    # This migration creates tables programmatically.
    op.create_table(
        "episodic_memory",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("user_id", sa.UUID(), nullable=False, index=True),
        # ... (full table definition)
    )
    # Enable extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS \"vector\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"pg_trgm\"")


def downgrade() -> None:
    """Leave destructive base-schema teardown as a manual operation."""
    pass
