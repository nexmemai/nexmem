"""Add FTS tsvector columns and GIN indexes

Revision ID: 004_add_fts_columns
Revises: 003_add_consolidated_at
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "004_add_fts_columns"
down_revision = "003_add_consolidated_at"
branch_labels = None
depends_on = None


def upgrade():
    # Add text_search column to episodic_memory
    op.add_column(
        'episodic_memory',
        sa.Column('text_search', TSVECTOR, nullable=True)
    )

    # Add text_search column to semantic_memory
    op.add_column(
        'semantic_memory',
        sa.Column('text_search', TSVECTOR, nullable=True)
    )

    # Create GIN indexes for full-text search
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodic_text_search "
        "ON episodic_memory USING GIN(text_search)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_semantic_text_search "
        "ON semantic_memory USING GIN(text_search)"
    )

    # Populate initial data
    op.execute(
        "UPDATE episodic_memory "
        "SET text_search = to_tsvector('english', COALESCE(content, ''))"
    )
    op.execute(
        "UPDATE semantic_memory "
        "SET text_search = to_tsvector('english', COALESCE(summary, ''))"
    )


def downgrade():
    # Drop indexes
    op.execute("DROP INDEX IF EXISTS idx_episodic_text_search")
    op.execute("DROP INDEX IF EXISTS idx_semantic_text_search")

    # Drop columns
    op.drop_column('episodic_memory', 'text_search')
    op.drop_column('semantic_memory', 'text_search')
