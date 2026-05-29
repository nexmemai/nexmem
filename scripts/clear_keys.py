"""Delete the 'Demo CLI Key' from the api_keys table.

Reads DATABASE_URL from the environment. Refuses to run with no
DATABASE_URL set so a leaked literal can never resurface here.
"""

import asyncio
import os
import sys

import asyncpg


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


async def clear_keys() -> None:
    conn = await asyncpg.connect(_resolve_db_url())
    try:
        print("Clearing existing 'Demo CLI Key' rows...")
        await conn.execute("DELETE FROM api_keys WHERE name = 'Demo CLI Key'")
        print("Cleared.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(clear_keys())
