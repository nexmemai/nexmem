"""Add consolidated_at to episodic_memory

Revision ID: 003_add_consolidated_at
Revises: 002_hnsw_index
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "003_add_consolidated_at"
down_revision = "002_hnsw_index"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE episodic_memory "
        "ADD COLUMN IF NOT EXISTS consolidated BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE episodic_memory "
        "ADD COLUMN IF NOT EXISTS consolidated_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE episodic_memory "
        "ADD COLUMN IF NOT EXISTS importance_score FLOAT NOT NULL DEFAULT 0.0"
    )


def downgrade():
    op.drop_column('episodic_memory', 'importance_score')
    op.drop_column('episodic_memory', 'consolidated_at')
    op.drop_column('episodic_memory', 'consolidated')
