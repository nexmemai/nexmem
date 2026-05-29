"""Add app_id to engrams for multi-app scoping.

Revision ID: 012_add_app_id_to_engrams
Revises: 011_fk_cascade_content_limits
Create Date: 2026-05-22

R-H5 (BACKEND_RISKS.md): the engrams table previously had no
`app_id`, so multi-app customers could not isolate engram contexts
between their apps. RLS on engrams continues to be user-scoped
(the policy in migration 008 references `user_id` only); per-app
filtering is enforced in the application layer at query time.

Backfill semantics:
  - Existing rows get app_id = NULL (i.e., "user-scoped, no app").
    This matches the existing semantic memory / episodic memory
    behaviour for legacy rows.
  - The column is indexed for read-time filtering by (user_id,
    app_id).

This migration is non-destructive and idempotent on re-run.
"""

from alembic import op
import sqlalchemy as sa


revision = "012_add_app_id_to_engrams"
down_revision = "011_fk_cascade_content_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the column only if it doesn't already exist (idempotent on re-run).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'engrams' AND column_name = 'app_id'
            ) THEN
                ALTER TABLE engrams ADD COLUMN app_id UUID NULL;
            END IF;
        END
        $$;
        """
    )

    # Index the column for (user_id, app_id) lookups.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_engrams_user_app "
        "ON engrams (user_id, app_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_engrams_user_app")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'engrams' AND column_name = 'app_id'
            ) THEN
                ALTER TABLE engrams DROP COLUMN app_id;
            END IF;
        END
        $$;
        """
    )
