"""Alembic environment.

This file MUST NOT contain any hardcoded credentials. The connection URL is
read exclusively from the DATABASE_URL environment variable. If it is unset,
the migration command exits with a clear error so a misconfigured deploy
fails loudly instead of silently using a stale or insecure default.
"""

import os
import re
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# ── Project import path ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables from .env (no-op if missing)
load_dotenv()

# ── Alembic config object ────────────────────────────────────────────────────
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import target metadata after sys.path is configured
from app.models import Base   # noqa: E402

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Read DATABASE_URL from env and normalize for Alembic (sync driver).

    Raises a clear, non-secret error if DATABASE_URL is missing.
    """
    raw = (os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        sys.stderr.write(
            "[alembic] DATABASE_URL is not set. Refusing to run migrations.\n"
            "          Set DATABASE_URL in your environment or .env file before invoking alembic.\n"
        )
        sys.exit(2)

    # Normalize driver: alembic uses sync drivers, our app uses asyncpg.
    if "+asyncpg" in raw:
        raw = raw.replace("+asyncpg", "+psycopg2")
    elif raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg2://", 1)
    elif raw.startswith("postgresql://") and "+psycopg2" not in raw:
        raw = raw.replace("postgresql://", "postgresql+psycopg2://", 1)

    # Force sslmode=require for psycopg2 (managed Postgres requires TLS).
    raw = re.sub(r"([?&])ssl[^=]*=[^&]*", "", raw, flags=re.I)
    raw = raw.replace("&&", "&").replace("?&", "?").rstrip("?&")
    raw += "&sslmode=require" if "?" in raw else "?sslmode=require"
    return raw


database_url = _resolve_database_url()

# ConfigParser interpolates % so escape it
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with an advisory lock.

    The advisory lock prevents concurrent replicas from racing on
    `alembic upgrade head` during a multi-replica deploy. Only the
    process that successfully acquires the lock runs migrations; the
    others observe a no-op and exit normally.
    """
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        echo=False,
    )

    # Stable, project-wide advisory lock id. Any int64 will do as long as it
    # is reused by every alembic invocation in this project.
    LOCK_ID = 728_419_362_001

    with connectable.connect() as connection:
        got_lock = connection.exec_driver_sql(
            f"SELECT pg_try_advisory_lock({LOCK_ID})"
        ).scalar()
        if not got_lock:
            sys.stderr.write(
                "[alembic] another process holds the migration lock; "
                "skipping migrations on this replica.\n"
            )
            return
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({LOCK_ID})")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
