"""Baseline migration for Supabase schema 001 + 002.

Revision ID: 001_baseline
Revises: None
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from typing import Sequence, Union

revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Baseline is handled by Supabase SQL migrations.
    This is a no-op to allow Alembic to track the versioning chain.
    """
    # Ensure extensions exist as a safety measure
    op.execute("CREATE EXTENSION IF NOT EXISTS \"vector\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"pg_trgm\"")


def downgrade() -> None:
    """Leave destructive base-schema teardown as a manual operation."""
    pass
