import asyncio
import os

import asyncpg


def get_database_url() -> str:
    database_url = os.environ["DATABASE_URL"].strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set before running this script.")
    return (
        database_url
        .replace("postgresql+asyncpg://", "postgresql://", 1)
        .replace("postgresql+psycopg2://", "postgresql://", 1)
    )

async def migrate_types():
    conn = await asyncpg.connect(get_database_url())
    try:
        print("[MIGRATE] Migrating user_id columns from TEXT to UUID...")
        tables = [
            "episodic_memory",
            "semantic_memory",
            "procedural_memory",
            "knowledge_nodes",
            "knowledge_edges"
        ]
        
        await conn.execute("DROP VIEW IF EXISTS memory_stats")
        await conn.execute("DROP VIEW IF EXISTS recent_memories")
        
        for table in tables:
            print(f"  Processing table: {table}")
            await conn.execute(f"DELETE FROM {table}")
            await conn.execute(f"ALTER TABLE {table} ALTER COLUMN user_id TYPE UUID USING user_id::uuid")
            print(f"  [OK] {table} user_id is now UUID")
            
        print("[DONE] Migration complete. Views dropped.")
        
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(migrate_types())
