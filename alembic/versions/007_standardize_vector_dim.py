"""Standardize semantic_memory vector dimension to 384D

Revision ID: 007_standardize_vector_dim
Revises: 006_align_app_scoping
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# Revision identifiers
revision = "007_standardize_vector_dim"
down_revision = "006_align_app_scoping"
branch_labels = None
depends_on = None


def upgrade():
    # Clear existing semantic vectors (incompatible dimensions)
    op.execute("DELETE FROM semantic_memory")
    
    # Alter vector column to 384 dimensions
    op.execute("ALTER TABLE semantic_memory ALTER COLUMN vector TYPE vector(384)")
    
    # Drop existing HNSW index if it exists
    op.execute("DROP INDEX IF EXISTS ix_semantic_vector_hnsw")
    
    # Recreate HNSW index for 384D vectors
    op.execute("""
        CREATE INDEX ix_semantic_vector_hnsw
        ON semantic_memory
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 200)
    """)
    
    # Update embedding_model default
    op.alter_column(
        "semantic_memory",
        "embedding_model",
        server_default="all-MiniLM-L6-v2"
    )


def downgrade():
    # Revert to 1536 dimensions (note: existing 384D vectors will be invalid)
    op.execute("ALTER TABLE semantic_memory ALTER COLUMN vector TYPE vector(1536)")
    
    # Drop the 384D index
    op.execute("DROP INDEX IF EXISTS ix_semantic_vector_hnsw")
    
    # Recreate 1536D index
    op.execute("""
        CREATE INDEX ix_semantic_vector_hnsw
        ON semantic_memory
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 200)
    """)
    
    # Revert embedding model default
    op.alter_column(
        "semantic_memory",
        "embedding_model",
        server_default="text-embedding-3-small"
    )
