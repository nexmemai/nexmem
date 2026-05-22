#!/usr/bin/env python3
"""Idempotent, race-safe alembic upgrade head.

Phase-1 deployment ran ``alembic upgrade head && uvicorn …`` in the
container start command. With multiple replicas (Render web + worker
+ beat all start at once), each one launched its own alembic process
against the same database; alembic's table-level locks usually
serialised them, but PgBouncer transaction-pooling and Postgres
deadlock detection made the outcome non-deterministic. R-H10.

This script:
  1. Opens a synchronous psycopg2 connection to DATABASE_URL.
  2. Takes a session-level Postgres advisory lock keyed on
     ``hash("nexmem-migrations")`` (a fixed 64-bit constant).
     This serialises every concurrent invocation: only one runs the
     upgrade; the others block until the lock releases, then see the
     schema is already at head and exit cleanly.
  3. Runs ``alembic upgrade head`` via the alembic Python API.
  4. Releases the lock.
  5. Logs wait + run durations.

Refusing to start the app on migration failure is the caller's job;
this script exits with the alembic exit status.

Usage (replaces the old start command):
    python scripts/run_migrations.py && uvicorn app.main:app ...
"""

from __future__ import annotations

import logging
import os
import sys
import time

# Fixed 64-bit advisory-lock key. Value is arbitrary but stable; do
# not change without coordinating with operators (changing the key
# means a stale instance running the OLD script could acquire a
# different lock and race a new instance).
ADVISORY_LOCK_KEY = 7_543_210_987_654_321  # noqa: WPS432 — magic number on purpose


def _resolve_db_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        sys.stderr.write(
            "run_migrations: DATABASE_URL is not set; refusing to migrate "
            "an unknown database.\n"
        )
        sys.exit(2)
    # alembic uses psycopg2 in sync mode; normalise the URL.
    if raw.startswith("postgresql+asyncpg://"):
        raw = raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    elif raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg2://", 1)
    elif raw.startswith("postgresql://") and "+psycopg2" not in raw:
        raw = raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [run_migrations] %(message)s"
    )
    log = logging.getLogger("run_migrations")

    db_url = _resolve_db_url()

    # alembic.ini lives at the repo root.
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    # Set the URL via sqlalchemy.url so alembic uses our resolved form
    # (and so env.py's _resolve_database_url also sees the same value
    # via the env var).
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

    # Sync engine for the advisory lock. We deliberately do not reuse
    # alembic's connection because alembic creates / drops its own
    # session per upgrade step.
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, future=True)
    wait_started = time.monotonic()
    with engine.connect() as conn:
        # pg_advisory_lock is session-level; releases on disconnect.
        log.info(
            "acquiring pg_advisory_lock(%d) — waits if another replica is migrating",
            ADVISORY_LOCK_KEY,
        )
        conn.execute(text(f"SELECT pg_advisory_lock({ADVISORY_LOCK_KEY})"))
        conn.commit()
        wait_elapsed = time.monotonic() - wait_started
        log.info("lock acquired after %.2fs; running 'alembic upgrade head'", wait_elapsed)

        run_started = time.monotonic()
        try:
            command.upgrade(cfg, "head")
        finally:
            # Always release the lock, even on failure.
            try:
                conn.execute(text(f"SELECT pg_advisory_unlock({ADVISORY_LOCK_KEY})"))
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                log.warning("pg_advisory_unlock failed (lock will release on disconnect): %s", exc)
        run_elapsed = time.monotonic() - run_started
        log.info("alembic upgrade head finished in %.2fs", run_elapsed)

    return 0


if __name__ == "__main__":
    sys.exit(main())
