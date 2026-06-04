# Nexmem — Current Status

- Date: 2026-06-04
- Branch: `main` (Post PR #27 merge)
- Python: 3.11.9
- Migration head (offline chain): `025_users_tier`

## Status: CI/CD PIPELINE STABILIZED AND MERGED

The CI/CD pipeline hardening has been completed and merged into `main` via PR #27. The integration test suite is now fully green against a real Postgres and Redis container stack.

## Verification & Fixes (PR #27)

- **Alembic Persistence Fixed:** Addressed `UndefinedTableError` by explicitly committing the SQLAlchemy 2.0 transaction during the `alembic/env.py` run when advisory locks are used.
- **Schema Drift Resolved:** Created `025_users_tier.py` to add the missing `tier` column to the `users` table.
- **Rate-Limiting Middleware:** Corrected ASGI middleware compatibility by updating `SlowAPIMiddleware` imports in `app/main.py` and disabling it conditionally for tests.
- **Integration Test Mocking & Bug Fix:** Diagnosed and fixed a hidden `UnboundLocalError` in `app/routers/memory.py` caused by a local `embedder` import within the demo mode block. This allowed the production-mode integration tests to successfully bypass the embedding service and use the mocked `dummy_embed` implementation without throwing false-positive 502 HTTP errors.
- **CI Pipeline Green:** All checks (integration-tests, migration-lint, secret-scan, flake8) pass successfully.

## Open PRs

- #1–#22 open; to be closed as superseded since all history and fixes are now correctly on `main`.

## Remaining Operator Actions (Deployment Checklist)

1. Close superseded PRs #1–#22 in GitHub.
2. Render env vars: Set `DATABASE_URL` (sync:false), `REDIS_URL`, `SECRET_KEY` (fresh), `SENTRY_DSN`, `ADMIN_API_KEY`; ensure `DEMO_MODE` is unset.
3. Apply Alembic migrations on live DB (head `025_users_tier`).
4. Rotate `SECRET_KEY` to invalidate pre-rewrite JWTs; ensure collaborators re-clone.
5. (Deferred) Publish `nexmem-py` / `nexmem-js` when ready.
