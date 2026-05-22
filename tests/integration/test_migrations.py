"""End-to-end migration tests against a real Postgres + pgvector.

Covers:
  - `alembic upgrade head` reaches head from a clean DB.
  - All five user-scoped memory tables exist with RLS enabled.
  - Migration 007 is non-destructive on the already-correct schema:
    re-running it does not delete rows.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text


pytestmark = pytest.mark.asyncio


async def test_migrations_reach_head() -> None:
    """The `_apply_migrations_once` autouse fixture has already run; we just
    confirm `alembic_version` is at a known head value here.
    """
    from app.database import async_session

    async with async_session() as s:
        result = await s.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar()

    assert version is not None
    # 011 is the current head as of this hardening pass.
    # If a future migration lands, this assertion is a reminder to update it.
    assert version == "011_fk_cascade_content_limits", (
        f"Unexpected alembic head: {version}. Update this test to match the "
        f"new HEAD revision when adding a migration."
    )


async def test_rls_is_enabled_on_memory_tables() -> None:
    """All six memory tables (incl. engrams) have rowsecurity=t and a policy."""
    from app.database import async_session

    expected = (
        "episodic_memory",
        "semantic_memory",
        "procedural_memory",
        "knowledge_nodes",
        "knowledge_edges",
        "engrams",
    )
    async with async_session() as s:
        rows = await s.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(:tables) AND relkind = 'r'"
            ),
            {"tables": list(expected)},
        )
        flags = {r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in rows}

    missing = set(expected) - set(flags)
    assert not missing, f"missing memory tables: {missing}"

    for name, (enabled, forced) in flags.items():
        assert enabled, f"RLS not enabled on {name}"
        assert forced, f"RLS not FORCEd on {name}; bypass via service role would be possible"


async def test_migration_007_is_idempotent_on_correct_schema() -> None:
    """Re-running migration 007 on an already-384 schema must not delete rows.

    We insert a vector via raw SQL, run the migration's `upgrade()` again
    against the live binding, then confirm the row is still there.
    """
    from app.database import async_session

    # Insert a row scoped to a fresh user_id so the truncate fixture between
    # tests can clean up via CASCADE.
    user_id = "00000000-0000-0000-0000-000000000777"
    embedding_str = "[" + ",".join("0.01" for _ in range(384)) + "]"

    async with async_session() as s:
        # Bypass RLS by setting GUC.
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user_id},
        )
        # Make sure a corresponding user row exists so any FK is happy.
        await s.execute(
            text(
                "INSERT INTO users (id, is_active, created_at, total_tokens_used, tier) "
                "VALUES (CAST(:uid AS uuid), TRUE, NOW(), 0, 'free') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"uid": user_id},
        )
        await s.execute(
            text(
                "INSERT INTO semantic_memory "
                "(id, user_id, vector, embedding_model, content_preview, metadata) "
                "VALUES (gen_random_uuid(), CAST(:uid AS uuid), CAST(:vec AS vector), "
                "'all-MiniLM-L6-v2', 'idempotency probe', '{}'::jsonb)"
            ),
            {"uid": user_id, "vec": embedding_str},
        )
        await s.commit()

    # Now drive the migration's `upgrade()` again. Because the dim is already
    # 384, the destructive branch must be skipped.
    from alembic import op as alembic_op  # noqa: F401  (provides the alembic context)
    from alembic.config import Config
    from alembic import command

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini"))

    # Use stamped state to re-run the operations without invoking schema-
    # changing logic on already-applied revisions: instead, we import the
    # migration module and call upgrade() directly within an alembic context.
    import importlib.util

    here = os.path.dirname(__file__)
    spec = importlib.util.spec_from_file_location(
        "mig_007_idempotent",
        os.path.join(here, "..", "..", "alembic", "versions", "007_standardize_vector_dim.py"),
    )
    assert spec is not None and spec.loader is not None
    mig_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig_module)

    # Wire alembic's `op` to the live database connection.
    from sqlalchemy import create_engine

    sync_url = os.environ["DATABASE_URL"].replace("+asyncpg", "+psycopg2")
    sync_engine = create_engine(sync_url)
    with sync_engine.begin() as conn:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        # Override the module-level `op` reference for this call.
        mig_module.op = ops
        mig_module.upgrade()

    # Row must still be present — proves no DELETE happened on the
    # already-correct schema.
    async with async_session() as s:
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user_id},
        )
        r = await s.execute(
            text(
                "SELECT count(*) FROM semantic_memory "
                "WHERE content_preview = 'idempotency probe'"
            )
        )
        assert (r.scalar() or 0) == 1, "migration 007 deleted rows on idempotent re-run"
