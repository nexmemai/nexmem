"""App-level RLS on the 5 memory tables (P4-B4).

Revision ID: 019_app_level_rls
Revises: 018_apikeys_app_fk
Create Date: 2026-05-23

Phase 4 — defense-in-depth multi-app isolation.

Tables affected
---------------
Exactly the 5 memory tables that already carry an ``app_id`` column
(set up in migrations 005 / 006 and modeled by ``_app_id_col`` in
``app/models/memory.py``):

    1. episodic_memory
    2. semantic_memory
    3. procedural_memory
    4. knowledge_nodes
    5. knowledge_edges

The ``engrams`` table also has RLS (migration 008) but does NOT have
an ``app_id`` column today, so it stays on the user-only policy. Adding
``engrams.app_id`` is a future migration.

Policy change
-------------
Migration 008 created policies named ``<table>_user_isolation`` that
filtered only on ``user_id = current_setting('app.current_user_id')``.
This migration replaces those 5 policies with an app-aware policy:

    user_id = current_setting('app.current_user_id')
    AND (
        app_id IS NULL
        OR
        app_id = current_setting('app.current_app_id')
    )

Backwards-compat semantics
--------------------------
- Rows with ``app_id IS NULL`` (legacy rows / requests that didn't pass
  an app_id) remain visible whether or not ``app.current_app_id`` is
  set. This preserves the single-app deployment story.
- Rows with ``app_id IS NOT NULL`` are visible **only** when
  ``app.current_app_id`` is set in the session and matches the row's
  ``app_id``. If the request never sets ``current_app_id``, those rows
  are invisible — that is the intended app-isolation semantics.
- Wiring ``app.current_app_id`` into the per-request middleware /
  Celery task path is a separate change. Until that lands, the safe
  reading is "single-app per user works as before; multi-app users
  should expect tighter scoping the moment current_app_id is set".

We use ``NULLIF(current_setting(..., true), '')::uuid`` so that an
unset GUC returns NULL (instead of raising), matching the convention
established in migration 008 and 013.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "019_app_level_rls"
down_revision: Union[str, None] = "018_apikeys_app_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Only the 5 memory tables that have an ``app_id`` column. ``engrams``
# is intentionally excluded — see module docstring.
APP_SCOPED_MEMORY_TABLES = (
    ("episodic_memory", "episodic_user_isolation"),
    ("semantic_memory", "semantic_user_isolation"),
    ("procedural_memory", "procedural_user_isolation"),
    ("knowledge_nodes", "knowledge_nodes_user_isolation"),
    ("knowledge_edges", "knowledge_edges_user_isolation"),
)

CURRENT_USER_EXPR = (
    "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
)
CURRENT_APP_EXPR = (
    "NULLIF(current_setting('app.current_app_id', true), '')::uuid"
)


def _app_scoped_using_clause() -> str:
    return (
        f"user_id = {CURRENT_USER_EXPR}"
        " AND ("
        f"app_id IS NULL OR app_id = {CURRENT_APP_EXPR}"
        ")"
    )


def upgrade() -> None:
    using = _app_scoped_using_clause()
    for table, policy in APP_SCOPED_MEMORY_TABLES:
        # Drop the user-only policy created in migration 008; recreate
        # it with the same name but app-aware semantics so every USING /
        # WITH CHECK call site keeps working without code churn.
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy}
            ON {table}
            FOR ALL
            USING ({using})
            WITH CHECK ({using})
            """
        )


def downgrade() -> None:
    # Restore the migration-008 user-only policy.
    user_only_clause = f"user_id = {CURRENT_USER_EXPR}"
    for table, policy in APP_SCOPED_MEMORY_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy}
            ON {table}
            FOR ALL
            USING ({user_only_clause})
            WITH CHECK ({user_only_clause})
            """
        )
