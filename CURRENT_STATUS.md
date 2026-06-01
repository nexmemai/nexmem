# Nexmem — Current Status

- Date: 2026-05-31
- Branch: `main`
- `main` tip SHA: `7a02202` (Merge pull request #23 from nexmemai/chore/p12-sdk-publish)
- PR #23 merge parent (hardening tip): `15fab12`
- Python: 3.11.9
- Migration head (offline chain): `024_app_suspension`

## Status: HARDENING STACK MERGED TO MAIN ✅ — beta-conditional on operator actions

PR #23 (`chore/p12-sdk-publish` → `main`) is **merged**. The full hardening
stack (Blocks 1–9 + history rewrite + Block 10–13 CI fixes) is now on `main`
at `7a02202`. `origin/main` was `c2f9212` before the merge.

## Post-merge verification (on main @ 7a02202)

- app imports: OK
- API key prefix: `nxm_`
- Secret scanner: clean (SHA-256 hash tripwire)
- Tests (`-m "not slow and not integration"`): 264 passed, 0 failed, 33 skipped, 5 deselected
- flake8 blocking gate (E9,F63,F7,F82): 0 findings
- Merge into main: completed (was verified conflict-free pre-merge)

## What is now on main

- 24 Alembic migrations (001_baseline → 024_app_suspension)
- All hardening work from Blocks 1–9
- TOTP 2FA, GDPR soft-delete, MCP hardening (Block 5)
- Operator CLI, force-logout, impersonation (Block 6)
- App metrics, suspension, queue backpressure (Block 7)
- SDK publish readiness, frontend auth audit (Block 8)
- SDK quickstart docs + e2e examples, with redacted output (Block 9)
- Timezone-aware JWT timestamps; secret scanner restored (hash tripwire)
- CI fixes: flake8 F821 imports, migration-lint annotations, pytest testpaths scope

## Open PRs

- #23: **merged**.
- #1–#22: to be **closed as superseded** (content is cumulative in #23).
  Batch command in `FINAL_MERGE_REPORT.md`.

## Remaining operator actions (see FINAL_MERGE_REPORT.md for full detail)

1. Close superseded PRs #1–#22.
2. Render env vars: `DATABASE_URL` (sync:false, no embedded creds), `REDIS_URL`,
   `SECRET_KEY` (fresh), `SENTRY_DSN`, `ADMIN_API_KEY`; ensure `DEMO_MODE` unset.
3. Apply Alembic migrations on the live DB via `scripts/run_with_migrations.sh`
   (target head `024_app_suspension`).
4. Rotate `SECRET_KEY` to invalidate pre-rewrite JWTs; ensure collaborators re-clone.
5. (Deferred) Publish nexmem-py to PyPI / nexmem-js to npm.

## Open risks (from BACKEND_RISKS.md)

- R-201: history rewrite COMPLETE (0 `db.*.supabase.co` matches in origin history).
- R-102: partial session revocation (token blocklist fails open on Redis outage).
- R-107: NetworkX graph single-worker only (enforced in render.yaml).
- R-301: Redis fail-open on auth / rate-limit paths (accepted for private beta).
