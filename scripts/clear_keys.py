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

async def clear_keys():
    conn = await asyncpg.connect(get_database_url())
    try:
        print("Clearing existing API keys for demo user...")
        await conn.execute("DELETE FROM api_keys WHERE name = 'Demo CLI Key'")
        print("Cleared.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(clear_keys())
