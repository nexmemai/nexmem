"""Baseline migration for Supabase schema 001 + 002.

Revision ID: 001_baseline
Revises: None
Create Date: 2026-04-27
"""

from alembic import op
from pathlib import Path
from typing import Sequence, Union

revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The canonical base schema lives in the Supabase SQL migrations. They are
# idempotent (CREATE TABLE/INDEX IF NOT EXISTS, CREATE OR REPLACE, DO-blocks
# guarding ADD COLUMN), so applying them here is a no-op on an existing
# Supabase database but creates the base tables on a fresh database (e.g. the
# CI alembic-roundtrip / integration Postgres, which would otherwise fail at
# 002_hnsw_index with 'relation "semantic_memory" does not exist' and at the
# engrams insert with 'relation "engrams" does not exist').
#
# Order matters: 001 creates the memory tables + app_id columns; 002 adds the
# auth tables (users, api_keys) and engrams, and no-ops the app_id ADDs that
# 001 already made.
_SUPABASE_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_BASE_SCHEMA_SQL = (
    _SUPABASE_DIR / "001_initial_schema.sql",
    _SUPABASE_DIR / "002_day2_auth_and_engrams.sql",
)


def upgrade() -> None:
    """
    Create the base schema from the idempotent Supabase SQL migrations.

    On Supabase the tables already exist, so every statement is a no-op
    (IF NOT EXISTS / CREATE OR REPLACE / guarded DO-blocks). On a clean
    database the base tables are created so the rest of the Alembic chain
    (and the integration tests) can run.
    """
    # Ensure extensions exist as a safety measure (also created by the SQL).
    op.execute("CREATE EXTENSION IF NOT EXISTS \"vector\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"pg_trgm\"")

    # Apply each idempotent base schema file if present. We guard on existence
    # so a packaging that omits the supabase/ tree (it is not part of the
    # runtime image) still upgrades cleanly against an existing database.
    for sql_path in _BASE_SCHEMA_SQL:
        print(f"DEBUG: sql_path={sql_path} is_file={sql_path.is_file()}", flush=True)
        if sql_path.is_file():
            op.execute(sql_path.read_text(encoding="utf-8"))


def downgrade() -> None:
    """
    Tear down the base schema created by the two Supabase SQL files.

    Intended for CI round-trip tests (alembic downgrade base) and local
    developer resets. In a real Supabase / production environment this
    migration is never run via Alembic — schema teardown there is a
    manual, operator-controlled operation.

    Drop order is the reverse of creation order so foreign-key
    constraints are satisfied:
      views / functions first (no FK deps, but reference tables)
      api_keys  (FK → users)
      engrams   (no FK to base tables)
      knowledge_edges  (FK → knowledge_nodes × 2)
      knowledge_nodes
      semantic_memory  (FK → episodic_memory)
      procedural_memory
      episodic_memory
      users
    Extensions (vector, uuid-ossp, pg_trgm) are left in place — they
    are cluster-level objects and removing them could break other
    databases on the same Postgres instance.
    """
    # Views and functions that reference the tables must go first.
    op.execute("DROP VIEW IF EXISTS recent_memories")
    op.execute("DROP VIEW IF EXISTS memory_stats")
    op.execute("DROP FUNCTION IF EXISTS cleanup_expired_episodic_memory()")

    # Tables in reverse FK dependency order.
    op.execute("DROP TABLE IF EXISTS api_keys")
    op.execute("DROP TABLE IF EXISTS engrams")
    op.execute("DROP TABLE IF EXISTS knowledge_edges")
    op.execute("DROP TABLE IF EXISTS knowledge_nodes")
    op.execute("DROP TABLE IF EXISTS semantic_memory")
    op.execute("DROP TABLE IF EXISTS procedural_memory")
    op.execute("DROP TABLE IF EXISTS episodic_memory")
    op.execute("DROP TABLE IF EXISTS users")
