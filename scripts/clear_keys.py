"""One-shot helper to clear demo CLI API keys.

Reads ``DATABASE_URL`` from the environment. Refuses to run if unset.
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


async def clear_keys() -> None:
    conn = await asyncpg.connect(_resolve_url())
    try:
        print("Clearing existing API keys for demo user...")
        await conn.execute("DELETE FROM api_keys WHERE name = 'Demo CLI Key'")
        print("Cleared.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(clear_keys())
