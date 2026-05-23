"""engrams.app_id + app-aware RLS (Amendment 2 to Phase 4).

Revision ID: 020_engrams_app_id
Revises: 019_app_level_rls
Create Date: 2026-05-23

Closes the gap left by migration 019: the ``engrams`` table also has
its own RLS policy (created in migration 008) but did not yet carry an
``app_id`` column, so app-level isolation could not be applied to it.
This migration:

1. Adds ``engrams.app_id UUID NULL`` with FK to ``apps.id`` and
   ``ON DELETE SET NULL`` (operator can re-bind without losing audit
   data — same posture as ``api_keys.app_id`` from migration 018).
2. Adds composite index ``(user_id, app_id)`` for the query patterns
   that filter by both (engram retrieval is user+app scoped).
3. Replaces the ``engrams_user_isolation`` RLS policy created in
   migration 008 with the same app-aware clause used by the 5 memory
   tables in migration 019:

       user_id = current_setting('app.current_user_id')::uuid
       AND (
           app_id IS NULL
           OR app_id = current_setting('app.current_app_id')::uuid
       )

   Backwards-compat: existing engrams have ``app_id IS NULL`` so they
   stay visible regardless of ``current_app_id``. Newly-written engrams
   that carry an ``app_id`` are isolated by app.

This migration intentionally leaves the ORM model + service layer
unchanged for engrams writes that don't set ``app_id``. Wiring the
engram processor to populate ``app_id`` from the source episode is a
separate change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "020_engrams_app_id"
down_revision: Union[str, None] = "019_app_level_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)
CURRENT_APP_EXPR = (
    "NULLIF(current_setting('app.current_app_id', true), '')::uuid"
)
APP_AWARE_CLAUSE = (
    f"user_id = {CURRENT_USER_EXPR}"
    " AND ("
    f"app_id IS NULL OR app_id = {CURRENT_APP_EXPR}"
    ")"
)
USER_ONLY_CLAUSE = f"user_id = {CURRENT_USER_EXPR}"


def upgrade() -> None:
    # 1. Add app_id column.
    op.add_column(
        "engrams",
        sa.Column("app_id", UUID(as_uuid=True), nullable=True),
    )
    # 2. FK to apps.id with SET NULL on delete.
    op.create_foreign_key(
        "fk_engrams_app_id",
        source_table="engrams",
        referent_table="apps",
        local_cols=["app_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    # 3. Composite index on (user_id, app_id) — the query path most
    # affected by app-level scoping.
    op.create_index(
        "ix_engrams_user_id_app_id",
        "engrams",
        ["user_id", "app_id"],
    )
    # 4. Replace the migration-008 user-only policy with the app-aware
    # policy.
    op.execute("DROP POLICY IF EXISTS engrams_user_isolation ON engrams")
    op.execute(
        f"""
        CREATE POLICY engrams_user_isolation
        ON engrams
        FOR ALL
        USING ({APP_AWARE_CLAUSE})
        WITH CHECK ({APP_AWARE_CLAUSE})
        """
    )


def downgrade() -> None:
    # Restore the user-only policy first so the schema is left in a
    # readable state if a subsequent step fails.
    op.execute("DROP POLICY IF EXISTS engrams_user_isolation ON engrams")
    op.execute(
        f"""
        CREATE POLICY engrams_user_isolation
        ON engrams
        FOR ALL
        USING ({USER_ONLY_CLAUSE})
        WITH CHECK ({USER_ONLY_CLAUSE})
        """
    )
    op.drop_index("ix_engrams_user_id_app_id", table_name="engrams")
    op.drop_constraint("fk_engrams_app_id", "engrams", type_="foreignkey")
    op.drop_column("engrams", "app_id")
