"""Align app scoping columns and procedural uniqueness.

Revision ID: 006_align_app_scoping
Revises: 005_add_app_id_procedural
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "006_align_app_scoping"
down_revision = "005_add_app_id_procedural"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS app_id UUID")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_edges_app_id "
        "ON knowledge_edges (app_id)"
    )

    op.execute(
        "ALTER TABLE procedural_memory "
        "DROP CONSTRAINT IF EXISTS procedural_memory_user_id_key"
    )
    op.execute(
        "ALTER TABLE procedural_memory "
        "ADD CONSTRAINT uq_procedural_user_app UNIQUE (user_id, app_id)"
    )


def downgrade():
    op.execute(
        "ALTER TABLE procedural_memory "
        "DROP CONSTRAINT IF EXISTS uq_procedural_user_app"
    )
    op.execute(
        "ALTER TABLE procedural_memory "
        "ADD CONSTRAINT procedural_memory_user_id_key UNIQUE (user_id)"
    )

    op.execute("DROP INDEX IF EXISTS idx_knowledge_edges_app_id")
    op.execute("ALTER TABLE knowledge_edges DROP COLUMN IF EXISTS app_id")
