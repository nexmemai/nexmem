"""Apply Supabase initial schema SQL files.

This script is a one-shot helper for applying the hand-written SQL files
under `supabase/migrations/`. The DATABASE_URL is read from the environment;
there is no hardcoded fallback.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/dbname \
        python scripts/apply_migrations.py
"""

import asyncio
import os
import sys
from urllib.parse import urlparse

import asyncpg


def _resolve_db_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        print(
            "ERROR: DATABASE_URL is not set. "
            "Refusing to apply migrations against an unknown database.",
            file=sys.stderr,
        )
        sys.exit(2)
    # asyncpg accepts postgres:// or postgresql:// but not the +asyncpg suffix.
    if raw.startswith("postgresql+asyncpg://"):
        raw = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    elif raw.startswith("postgresql+psycopg2://"):
        raw = raw.replace("postgresql+psycopg2://", "postgresql://", 1)
    parsed = urlparse(raw)
    if parsed.scheme not in {"postgres", "postgresql"}:
        print(f"ERROR: Unsupported DATABASE_URL scheme: {parsed.scheme}", file=sys.stderr)
        sys.exit(2)
    return raw


async def run_sql_file(conn: asyncpg.Connection, file_path: str) -> None:
    print(f"Running {file_path}...")
    if not os.path.exists(file_path):
        print(f"  File not found: {file_path}")
        return
    with open(file_path, "r", encoding="utf-8") as f:
        sql = f.read()
    try:
        await conn.execute(sql)
        print(f"  OK: applied {file_path}")
    except Exception as exc:  # noqa: BLE001 — script-level error surface
        print(f"  ERROR applying {file_path}: {exc}")
        raise


async def main() -> None:
    db_url = _resolve_db_url()
    print("Connecting to Postgres...")
    conn = await asyncpg.connect(db_url)
    try:
        await run_sql_file(conn, "supabase/migrations/001_initial_schema.sql")
        await run_sql_file(conn, "supabase/migrations/002_day2_auth_and_engrams.sql")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
