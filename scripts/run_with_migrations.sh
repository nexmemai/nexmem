#!/usr/bin/env bash
#
# Run alembic migrations under a Postgres advisory lock, then exec the
# requested command. Designed for Render-style multi-replica startup
# where every replica runs the same start command.
#
# The advisory lock is acquired inside alembic/env.py itself; if another
# replica holds the lock, alembic returns immediately without applying
# anything. That makes this wrapper safe to run on every replica.
#
# Usage:
#   scripts/run_with_migrations.sh <command> [args...]
#
# Example:
#   scripts/run_with_migrations.sh uvicorn app.main:app --host 0.0.0.0 --port $PORT
#
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[run_with_migrations] DATABASE_URL is unset; refusing to start." >&2
    exit 2
fi

echo "[run_with_migrations] running alembic upgrade head ..."
alembic upgrade head

echo "[run_with_migrations] starting application: $*"
exec "$@"
