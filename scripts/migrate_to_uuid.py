import asyncio
import asyncpg

DB_URL = "postgresql://postgres:***REDACTED_PASSWORD***@db.***REDACTED_PROJECT_ID***.supabase.co:5432/postgres"

async def migrate_types():
    conn = await asyncpg.connect(DB_URL)
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
