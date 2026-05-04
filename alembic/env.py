print("[EMERGENCY DEBUG] Alembic env.py starting...")
import sys
import os
import re
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

print("[EMERGENCY DEBUG] Imports successful, setting path...")
# ✅ Add project root to Python path so 'app' module is found on Render
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata = None
from app.models import Base
target_metadata = Base.metadata

# Get DATABASE_URL from environment
database_url = os.getenv('DATABASE_URL', '').strip()

if not database_url:
    # Fallback to hardcoded settings if env is missing
    from app.config import settings
    database_url = settings.database_url

# ✅ Handle asyncpg prefix - convert to psycopg2 for Alembic sync mode
if "+asyncpg" in database_url:
    database_url = database_url.replace("+asyncpg", "+psycopg2")
elif database_url.startswith('postgresql://'):
    database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

# ✅ Update the sqlalchemy.url in config (escape % for interpolation)
config.set_main_option('sqlalchemy.url', database_url.replace("%", "%%"))

print(f"[Alembic] Connecting to: {database_url[:60]}...")

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
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
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")
    
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        echo=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
