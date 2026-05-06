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

# 🛑 FAIL-SAFE OVERRIDE: 
# If Render dashboard has a stale/broken IPv6 hostname, force the Tokyo pooler.
if "db.***REDACTED_PROJECT_ID***" in database_url or not database_url:
    print("[Alembic] Stale/Missing DATABASE_URL detected. Forcing Tokyo pooler override.")
    database_url = "postgresql://postgres.***REDACTED_PROJECT_ID***:***REDACTED_PASSWORD***@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require"

# ✅ Handle asyncpg prefix - convert to psycopg2 for Alembic sync mode
# ✅ Handle asyncpg prefix - convert to psycopg2 for Alembic sync mode
if "+asyncpg" in database_url:
    database_url = database_url.replace("+asyncpg", "+psycopg2")
elif database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql+psycopg2://', 1)
elif database_url.startswith('postgresql://'):
    database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

# ✅ Fix SSL parameter for psycopg2 (Alembic) compatibility
# 1. First, strip out ANY existing ssl parameters (ssl, SSL, sslmode, etc.) to start clean
database_url = re.sub(r'([?&])ssl[^=]*=[^&]*', '', database_url, flags=re.I)
# 2. Clean up any double ampersands or trailing separators left by the stripping
database_url = database_url.replace("&&", "&").replace("?&", "?").rstrip("?&")
# 3. Forcefully add the correct psycopg2 parameter
if "?" in database_url:
    database_url += "&sslmode=require"
else:
    database_url += "?sslmode=require"

# ✅ Update the sqlalchemy.url in config (escape % for interpolation)
final_url = database_url.replace("%", "%%")
config.set_main_option('sqlalchemy.url', final_url)

print(f"[Alembic] Final Connection URL (masked): {database_url.split('@')[-1] if '@' in database_url else 'hidden'}")
sys.stdout.flush()

print(f"[Alembic] Connecting to: {database_url[:60]}...")
sys.stdout.flush()

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
