"""Add app_id to procedural_memory

Revision ID: 005_add_app_id_procedural
Revises: 004_add_fts_columns
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


def upgrade():
    # Add app_id column to procedural_memory
    op.add_column(
        'procedural_memory',
        sa.Column('app_id', UUID(as_uuid=True), nullable=True)
    )

    # Create index on app_id for query performance
    op.create_index(
        'idx_procedural_app_id',
        'procedural_memory',
        ['app_id'],
        unique=False
    )


def downgrade():
    # Drop index
    op.drop_index('idx_procedural_app_id', table_name='procedural_memory')

    # Drop column
    op.drop_column('procedural_memory', 'app_id')
