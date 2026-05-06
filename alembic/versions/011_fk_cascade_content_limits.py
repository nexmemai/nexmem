"""add fk cascade and content length constraints

Revision ID: 011_fk_cascade_content_limits
Revises: 010_token_usage
Create Date: 2026-05-07

Changes:
- Add FK from api_keys.user_id -> users.id with ON DELETE CASCADE
- Add FK from token_usage.user_id -> users.id with ON DELETE CASCADE
- Add max-length CHECK constraints on text content columns to prevent
  abuse via oversized payloads (32 768 chars ≈ ~64 KB of UTF-8).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '011_fk_cascade_content_limits'
down_revision: Union[str, None] = '010_token_usage'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MAX_CONTENT = 32768   # characters


def upgrade() -> None:
    # ── Foreign key: api_keys → users (CASCADE on delete) ────────────────────
    op.create_foreign_key(
        'fk_api_keys_user_id',
        'api_keys', 'users',
        ['user_id'], ['id'],
        ondelete='CASCADE',
    )

    # ── Foreign key: token_usage → users (CASCADE on delete) ─────────────────
    op.create_foreign_key(
        'fk_token_usage_user_id',
        'token_usage', 'users',
        ['user_id'], ['id'],
        ondelete='CASCADE',
    )

    # ── Content-length CHECK constraints ──────────────────────────────────────
    # episodic_memory.content
    op.create_check_constraint(
        'ck_episodic_content_length',
        'episodic_memory',
        f'length(content) <= {_MAX_CONTENT}',
    )

    # semantic_memory.content (text column may be named content or text_content)
    # Use a safe alter that only applies if the column exists
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'semantic_memory'
                  AND column_name = 'content'
            ) THEN
                ALTER TABLE semantic_memory
                    ADD CONSTRAINT ck_semantic_content_length
                    CHECK (length(content) <= {_MAX_CONTENT});
            END IF;
        END
        $$;
        """
    )

    # procedural_memory.content
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'procedural_memory'
                  AND column_name = 'content'
            ) THEN
                ALTER TABLE procedural_memory
                    ADD CONSTRAINT ck_procedural_content_length
                    CHECK (length(content) <= {_MAX_CONTENT});
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE procedural_memory DROP CONSTRAINT IF EXISTS ck_procedural_content_length")
    op.execute("ALTER TABLE semantic_memory DROP CONSTRAINT IF EXISTS ck_semantic_content_length")
    op.drop_constraint('ck_episodic_content_length', 'episodic_memory', type_='check')
    op.drop_constraint('fk_token_usage_user_id', 'token_usage', type_='foreignkey')
    op.drop_constraint('fk_api_keys_user_id', 'api_keys', type_='foreignkey')
