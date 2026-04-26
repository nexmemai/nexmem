"""
Alembic async env.py — wired to FastAPI async engine.

Key decisions:
- Uses asyncio "run_migrations_online" pattern (required for asyncpg).
- Pulls DATABASE_URL from app.config.settings so a single source of truth.
- Imports app.models so all table metadata is registered before autogenerate.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

# ── app imports ────────────────────────────────────────────────────────────────
from app.config import settings

# Import ALL models so their tables appear in metadata (critical for autogenerate)
import app.models  # noqa: F401 — side-effect import registers tables
from app.database import Base

# ── Alembic config ─────────────────────────────────────────────────────────────
config = context.config

# Wire in Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for --autogenerate
target_metadata = Base.metadata

# Override the sqlalchemy.url with the real URL from settings (escape % for configparser)
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))


# ── offline mode (generates SQL without connecting) ───────────────────────────
def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection.
    Useful for generating a SQL script to review before applying.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ── online async mode (connects and applies migrations) ───────────────────────
def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Include schemas if you ever add Postgres schemas
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """
    Run migrations with a live async connection.
    NullPool is used so Alembic doesn't hang on to connections.
    """
    connectable = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        echo=False,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# ── entry point ────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
