"""DESTRUCTIVE one-shot migration: convert ``user_id`` columns to UUID.

This script truncates all memory tables. It is preserved for historical
reference only; production should use the Alembic migration set under
``alembic/versions/``. Reads ``DATABASE_URL`` from env. Requires the
explicit ``CONFIRM=YES_DELETE_ALL_MEMORY`` env var to proceed.
"""
import asyncio
import os
import sys

import asyncpg


def _resolve_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        sys.stderr.write("DATABASE_URL is not set. Refusing to run.\n")
        sys.exit(2)
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
    return url


def _require_confirmation() -> None:
    if os.getenv("CONFIRM") != "YES_DELETE_ALL_MEMORY":
        sys.stderr.write(
            "This script wipes every memory table. Set "
            "CONFIRM=YES_DELETE_ALL_MEMORY to proceed.\n"
        )
        sys.exit(2)


async def migrate_types() -> None:
    _require_confirmation()
    conn = await asyncpg.connect(_resolve_url())
    try:
        print("[MIGRATE] Converting user_id columns from TEXT to UUID...")
        tables = [
            "episodic_memory",
            "semantic_memory",
            "procedural_memory",
            "knowledge_nodes",
            "knowledge_edges",
        ]
        await conn.execute("DROP VIEW IF EXISTS memory_stats")
        await conn.execute("DROP VIEW IF EXISTS recent_memories")
        for table in tables:
            print(f"  Processing table: {table}")
            await conn.execute(f"DELETE FROM {table}")
            await conn.execute(
                f"ALTER TABLE {table} ALTER COLUMN user_id TYPE UUID USING user_id::uuid"
            )
            print(f"  [OK] {table} user_id is now UUID")
        print("[DONE] Migration complete. Views dropped.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate_types())
