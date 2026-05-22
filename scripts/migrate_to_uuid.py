"""One-time data migration: convert legacy TEXT user_id columns to UUID.

Reads DATABASE_URL from the environment. Refuses to run if unset.

WARNING: this script DROPS rows. It is destructive and should only be
run on an environment that has been explicitly approved for the change.
Set ALLOW_DESTRUCTIVE_MIGRATION=1 to confirm.
"""

import asyncio
import os
import sys

import asyncpg


TABLES = (
    "episodic_memory",
    "semantic_memory",
    "procedural_memory",
    "knowledge_nodes",
    "knowledge_edges",
)


def _resolve_db_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        print(
            "ERROR: DATABASE_URL is not set. Refusing to operate on an unknown database.",
            file=sys.stderr,
        )
        sys.exit(2)
    if raw.startswith("postgresql+asyncpg://"):
        raw = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif raw.startswith("postgresql+psycopg2://"):
        raw = raw.replace("postgresql+psycopg2://", "postgresql://", 1)
    return raw


def _require_destructive_flag() -> None:
    if os.getenv("ALLOW_DESTRUCTIVE_MIGRATION") != "1":
        print(
            "ERROR: this script DELETEs all rows from "
            f"{', '.join(TABLES)} and changes column types.\n"
            "Set ALLOW_DESTRUCTIVE_MIGRATION=1 to acknowledge and proceed.",
            file=sys.stderr,
        )
        sys.exit(2)


async def migrate_types() -> None:
    _require_destructive_flag()
    conn = await asyncpg.connect(_resolve_db_url())
    try:
        print("[MIGRATE] Migrating user_id columns from TEXT to UUID...")
        await conn.execute("DROP VIEW IF EXISTS memory_stats")
        await conn.execute("DROP VIEW IF EXISTS recent_memories")
        for table in TABLES:
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
