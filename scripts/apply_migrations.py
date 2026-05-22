"""One-shot helper to apply legacy ``supabase/migrations/*.sql`` files.

Modern migrations live under ``alembic/versions/``. This script is kept
for historical reference only; new schema work must use Alembic.

The connection string is read from ``DATABASE_URL`` and must point at
the target Postgres instance. The script refuses to run if the env var
is unset.
"""
import asyncio
import os
import sys

import asyncpg


def _resolve_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        sys.stderr.write(
            "DATABASE_URL is not set. Refusing to run.\n"
            "Set it in your environment before invoking this script.\n"
        )
        sys.exit(2)
    # asyncpg wants postgres:// or postgresql:// without +driver
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
    return url


async def run_sql_file(conn, file_path: str) -> None:
    print(f"Running {file_path}...")
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return
    with open(file_path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    try:
        await conn.execute(sql)
        print(f"Successfully applied {file_path}")
    except Exception as exc:
        print(f"Error applying {file_path}: {exc}")


async def main() -> None:
    db_url = _resolve_url()
    print("Connecting...")
    conn = await asyncpg.connect(db_url)
    try:
        await run_sql_file(conn, "supabase/migrations/001_initial_schema.sql")
        await run_sql_file(conn, "supabase/migrations/002_day2_auth_and_engrams.sql")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
