"""Add HNSW index to engrams.dense_embedding column.

Revision ID: 009_engram_hnsw_index
Revises: 008_enable_memory_rls
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# Revision identifiers
revision = "009_engram_hnsw_index"
down_revision = "008_enable_memory_rls"
branch_labels = None
depends_on = None


def upgrade():
    """Add HNSW index for dense_embedding in engrams table."""
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'engrams') THEN
                CREATE INDEX IF NOT EXISTS idx_engrams_hnsw
                ON engrams
                USING hnsw (dense_embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 200);
            END IF;
        END
        $$;
    """)


def downgrade():
    """Remove HNSW index from engrams."""
    op.execute("DROP INDEX IF EXISTS idx_engrams_hnsw")
