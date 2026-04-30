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
    """Create the base schema used by fresh environments."""
    project_root = Path(__file__).resolve().parents[2]
    for migration in (
        project_root / "supabase" / "migrations" / "001_initial_schema.sql",
        project_root / "supabase" / "migrations" / "002_day2_auth_and_engrams.sql",
    ):
        op.get_bind().exec_driver_sql(migration.read_text(encoding="utf-8"))


def downgrade() -> None:
    """Leave destructive base-schema teardown as a manual operation."""
    pass
