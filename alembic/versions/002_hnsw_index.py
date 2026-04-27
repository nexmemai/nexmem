"""Switch to HNSW index for semantic memory.

Note: Only run this after you have >1000 vectors.
HNSW is better for continuous insertion patterns.
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_hnsw_index"
down_revision: Union[str, None] = "001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop IVFFlat if exists, add HNSW index."""
    op.execute("DROP INDEX IF EXISTS idx_semantic_memory_embedding")

    op.execute("""
        CREATE INDEX idx_semantic_memory_hnsw
        ON semantic_memory
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    """Remove HNSW index."""
    op.execute("DROP INDEX IF EXISTS idx_semantic_memory_hnsw")