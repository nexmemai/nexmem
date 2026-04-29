"""Add consolidated_at to episodic_memory

Revision ID: 003_add_consolidated_at
Revises: 002_hnsw_index
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP
from datetime import datetime


def upgrade():
    # Add consolidated_at column to episodic_memory
    op.add_column(
        'episodic_memory',
        sa.Column('consolidated_at', TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade():
    # Drop consolidated_at column
    op.drop_column('episodic_memory', 'consolidated_at')
