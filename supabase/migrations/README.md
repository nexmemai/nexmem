# Supabase Migrations — Reference Only

## Purpose

These SQL files are **human-readable schema documentation** and can be used to manually recover a blank Supabase instance via the SQL Editor.

## ⚠️ Important

**Alembic is the single source of truth.**

- `alembic/versions/*.py` — **These are the real migrations**. They run automatically via `alembic upgrade head` in `render.yaml`.
- `supabase/migrations/*.sql` — **These are reference only**. Do NOT run them via Alembic. Do NOT add them to `alembic/versions/001_baseline.py`.

## If you need to recover a blank Supabase instance:

1. Go to your Supabase project → **SQL Editor**
2. Copy-paste and run these files in order:
   - `001_initial_schema.sql` (base tables, extensions, indexes)
   - `002_day2_auth_and_engrams.sql` (auth tables, engram tables)
3. Then run `alembic upgrade head` to catch up on newer migrations (HNSW, RLS, vector dim standardization).

## File List

| File | Description |
|------|-------------|
| `001_initial_schema.sql` | Base schema: episodic, semantic, procedural, knowledge tables |
| `002_day2_auth_and_engrams.sql` | Users, API keys, engram tables, HNSW indexes |

## Why keep both?

- **Supabase SQL**: Required for Supabase CLI local dev or emergency SQL Editor recovery.
- **Alembic**: Required for Render deployment (runs automatically in `startCommand`).

Deleting either breaks a critical deployment path.
