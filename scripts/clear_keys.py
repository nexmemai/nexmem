import asyncio
import asyncpg

DB_URL = "postgresql://postgres:***REDACTED_PASSWORD***@db.***REDACTED_PROJECT_ID***.supabase.co:5432/postgres"

async def clear_keys():
    conn = await asyncpg.connect(DB_URL)
    try:
        print("Clearing existing API keys for demo user...")
        await conn.execute("DELETE FROM api_keys WHERE name = 'Demo CLI Key'")
        print("Cleared.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(clear_keys())
