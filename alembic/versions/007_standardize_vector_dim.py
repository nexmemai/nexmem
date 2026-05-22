"""Standardize semantic_memory vector dimension to 384D — non-destructive when possible.

Revision ID: 007_standardize_vector_dim
Revises: 006_align_app_scoping
Create Date: 2026-04-30

History:
    The original revision unconditionally ran `DELETE FROM semantic_memory`
    on `upgrade()`. That meant any environment that ran `alembic upgrade head`
    from before-007 lost all semantic memory rows, even if the column was
    already 384-dim (e.g. a fresh install hand-applied via Supabase SQL).
    Recovering from a backup at revision 006 and replaying migrations would
    silently destroy data.

What this revision does now:
    1. Detect the current vector dimension via pg_attribute.atttypmod.
    2. If the column is already vector(384):
       - Skip the DELETE entirely.
       - Recreate the HNSW index (idempotent).
       - Update the embedding_model server default.
    3. If the column is a different dimension (e.g. legacy 1536):
       - Refuse to proceed unless ALLOW_DESTRUCTIVE_MIGRATION=1 is set in the
         environment. This prevents an accidental `alembic upgrade head` from
         wiping production data.
       - When the flag is set, DELETE rows, ALTER the column, and rebuild the
         index. The number of rows that would be destroyed is logged via
         RAISE NOTICE so the operator sees what they consented to.
"""

import os

from alembic import op


revision = "007_standardize_vector_dim"
down_revision = "006_align_app_scoping"
branch_labels = None
depends_on = None


_TARGET_DIM = 384
_ALLOW_DESTRUCTIVE_ENV = "ALLOW_DESTRUCTIVE_MIGRATION"


def _current_vector_dim() -> int | None:
    """Return the current vector dimension of semantic_memory.vector, or None
    if the column / table is missing.

    pgvector encodes the declared dimension in pg_attribute.atttypmod
    (verified empirically: vector(384) → atttypmod=384). We use raw SQL
    because there is no SQLAlchemy reflector for pgvector dimension yet.
    """
    from sqlalchemy import text

    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT a.atttypmod "
            "FROM pg_attribute a "
            "JOIN pg_class c ON a.attrelid = c.oid "
            "WHERE c.relname = 'semantic_memory' AND a.attname = 'vector' "
            "  AND NOT a.attisdropped"
        )
    )
    row = result.first()
    if row is None:
        return None
    dim = row[0]
    return int(dim) if dim is not None and dim > 0 else None


def _row_count_semantic_memory() -> int:
    from sqlalchemy import text

    bind = op.get_bind()
    result = bind.execute(text("SELECT COUNT(*) FROM semantic_memory"))
    return int(result.scalar() or 0)


def upgrade() -> None:
    current_dim = _current_vector_dim()

    if current_dim == _TARGET_DIM:
        # Already correct shape. Make sure the index and default are right
        # without touching any rows.
        op.execute("DROP INDEX IF EXISTS ix_semantic_vector_hnsw")
        op.execute(
            "CREATE INDEX ix_semantic_vector_hnsw "
            "ON semantic_memory "
            "USING hnsw (vector vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 200)"
        )
        op.alter_column(
            "semantic_memory",
            "embedding_model",
            server_default="all-MiniLM-L6-v2",
        )
        return

    # Different dim (or column missing). The change requires destroying rows
    # because pgvector cannot cast between dimensions. Demand explicit consent.
    if os.getenv(_ALLOW_DESTRUCTIVE_ENV) != "1":
        rows = _row_count_semantic_memory() if current_dim is not None else 0
        raise RuntimeError(
            f"Migration 007 would change semantic_memory.vector from "
            f"vector({current_dim}) to vector({_TARGET_DIM}) and DELETE "
            f"{rows} existing row(s). Refusing to proceed without explicit "
            f"consent. Set {_ALLOW_DESTRUCTIVE_ENV}=1 to acknowledge data "
            f"loss and re-run `alembic upgrade head`."
        )

    # Consent given: log the row count, then DELETE and ALTER.
    op.execute(
        "DO $$ DECLARE n bigint; BEGIN "
        "SELECT COUNT(*) INTO n FROM semantic_memory; "
        "RAISE NOTICE 'Migration 007: deleting % semantic_memory rows for "
        "dimension change to vector(384)', n; "
        "END $$;"
    )
    op.execute("DELETE FROM semantic_memory")
    op.execute(f"ALTER TABLE semantic_memory ALTER COLUMN vector TYPE vector({_TARGET_DIM})")
    op.execute("DROP INDEX IF EXISTS ix_semantic_vector_hnsw")
    op.execute(
        "CREATE INDEX ix_semantic_vector_hnsw "
        "ON semantic_memory "
        "USING hnsw (vector vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 200)"
    )
    op.alter_column(
        "semantic_memory",
        "embedding_model",
        server_default="all-MiniLM-L6-v2",
    )


def downgrade() -> None:
    """Revert to 1536 dimensions.

    Going from 384 to 1536 also cannot preserve existing rows. Same
    `ALLOW_DESTRUCTIVE_MIGRATION` gate applies.
    """
    current_dim = _current_vector_dim()
    if current_dim == 1536:
        # Already at downgrade target. Make sure the index is rebuilt for
        # 1536-dim vectors.
        op.execute("DROP INDEX IF EXISTS ix_semantic_vector_hnsw")
        op.execute(
            "CREATE INDEX ix_semantic_vector_hnsw "
            "ON semantic_memory "
            "USING hnsw (vector vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 200)"
        )
        op.alter_column(
            "semantic_memory",
            "embedding_model",
            server_default="text-embedding-3-small",
        )
        return

    if os.getenv(_ALLOW_DESTRUCTIVE_ENV) != "1":
        rows = _row_count_semantic_memory() if current_dim is not None else 0
        raise RuntimeError(
            f"Migration 007 downgrade would change semantic_memory.vector from "
            f"vector({current_dim}) to vector(1536) and DELETE {rows} "
            f"existing row(s). Refusing to proceed without explicit consent. "
            f"Set {_ALLOW_DESTRUCTIVE_ENV}=1 to acknowledge."
        )

    op.execute("DELETE FROM semantic_memory")
    op.execute("ALTER TABLE semantic_memory ALTER COLUMN vector TYPE vector(1536)")
    op.execute("DROP INDEX IF EXISTS ix_semantic_vector_hnsw")
    op.execute(
        "CREATE INDEX ix_semantic_vector_hnsw "
        "ON semantic_memory "
        "USING hnsw (vector vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 200)"
    )
    op.alter_column(
        "semantic_memory",
        "embedding_model",
        server_default="text-embedding-3-small",
    )
