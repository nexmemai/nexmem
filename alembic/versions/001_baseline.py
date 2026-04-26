"""Baseline migration — stamps Alembic to match Supabase migrations 001 + 002.

This migration does NOT create tables — they already exist in Supabase.
It records the current schema state so future `alembic revision --autogenerate`
commands only detect *new* changes.

Revision ID: 001
Revises: None
Create Date: 2026-04-27

To apply:
    alembic upgrade head

To generate next migration:
    alembic revision --autogenerate -m "describe_your_change"
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Baseline — all tables already exist via Supabase SQL migrations.
    This is a no-op; it just stamps the DB so Alembic knows where we are.
    """
    pass


def downgrade() -> None:
    """
    Dropping everything is intentionally left as a manual operation.
    Use Supabase dashboard or psql to drop tables if needed.
    """
    pass
