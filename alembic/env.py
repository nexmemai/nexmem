"""Alembic environment.

The DATABASE_URL is read from the environment (or a project `.env` file).
There is no hardcoded fallback. If DATABASE_URL is unset or empty, the
migration runner raises immediately so we never accidentally connect to a
default / shared database with a leaked credential.
"""

import os
import re
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Add project root so `app` is importable when alembic is invoked from CWD.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.models import Base  # noqa: E402

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Resolve the DATABASE_URL strictly from the environment.

    No hardcoded fallback. Hard-fail with a clear error message if missing.
    """
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise RuntimeError(
            "DATABASE_URL is not set. Refusing to run migrations against an "
            "unknown database. Set DATABASE_URL in your environment, .env "
            "file, or your deployment provider (e.g. Render dashboard)."
        )
    return raw


def _normalise_for_alembic(url: str) -> str:
    """Normalise the URL for alembic's sync (psycopg2) engine.

    - asyncpg → psycopg2
    - postgres:// → postgresql+psycopg2://
    - strip any ssl/sslmode params then re-add sslmode=require for psycopg2
    """
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "+psycopg2")
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    # Strip any existing ssl-related params; re-add sslmode=require for psycopg2.
    url = re.sub(r"([?&])ssl[^=]*=[^&]*", "", url, flags=re.IGNORECASE)
    url = url.replace("&&", "&").replace("?&", "?").rstrip("?&")
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}sslmode=require"
    return url


database_url = _normalise_for_alembic(_resolve_database_url())

# Escape % for ConfigParser-style interpolation.
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

# Mask credentials in logs.
_masked = database_url.split("@")[-1] if "@" in database_url else "<no-host>"
print(f"[Alembic] Connecting to: {_masked}")
sys.stdout.flush()


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        echo=False,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
