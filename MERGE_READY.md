# MERGE READY — PR #23 (chore/p12-sdk-publish → main) — MERGED ✅

- Date/time: 2026-05-31
- PR #23: **MERGED** into `main`
- `main` tip after merge: `7a02202` (Merge pull request #23 …)
- Hardening tip merged: `15fab12`
- Branch: `chore/p12-sdk-publish` (merged), docs refreshed from `main`

## Final local verification results

| Check | Command | Result |
|-------|---------|--------|
| Secret scanner | `python scripts/scan_secrets.py` | PASS — clean (no matches in tracked files) |
| Tests (`tests/` scope) | `pytest tests/ -q` | PASS — 264 passed, 0 failed, 33 skipped, 5 deselected |
| Tests (CI scope) | `pytest -m "not slow and not integration"` | PASS — 279 passed, 0 failed, 33 skipped, 5 deselected |
| flake8 blocking gate | `flake8 app --select=E9,F63,F7,F82 --count` | PASS — 0 |
| JS quickstart syntax | `node --check examples/javascript_quickstart.mjs` | PASS — clean |
| Python quickstart compile | `py_compile examples/python_quickstart.py` | PASS — clean |

### Note on `flake8 app/ --count` (full, default config)
The bare `flake8 app/ --count` reports **595** findings, but these are
**all non-blocking** E501 "line too long" style warnings under flake8's
default 79-char limit. CI runs flake8 in two steps:
1. **Blocking:** `--select=E9,F63,F7,F82` → **0 findings** (the gate that fails CI).
2. **Non-blocking:** `--exit-zero --max-line-length=120` → warnings only, never fails the build.
No reformatting was done (out of scope: no new work). The CI-blocking gate is clean.

## Merge readiness

**PR #23 is MERGED into `main` (tip `7a02202`).** The pre-merge checks above
passed; post-merge verification on `main` re-confirmed: app imports OK,
scanner clean, 264 passed / 0 failed, flake8 blocking gate 0.

## Operator steps remaining (post-merge)

1. **Close PRs #1–#22 as superseded** — their content is cumulative in #23.
   The exact `gh pr close` batch command is in `FINAL_MERGE_REPORT.md`.
2. Complete the deployment gates below before real beta traffic.

## Post-merge deployment gates (before real beta traffic)

- Render env vars: `DATABASE_URL` (sync:false, no embedded creds), `REDIS_URL`,
  `SECRET_KEY` (fresh value), `SENTRY_DSN`, `ADMIN_API_KEY`; ensure `DEMO_MODE` unset.
- Apply Alembic migrations on the live DB via `scripts/run_with_migrations.sh`
  (target head `024_app_suspension`).
- Rotate `SECRET_KEY` to invalidate any pre-rewrite JWTs.
- Ensure all collaborators delete old clones and re-clone (post history-rewrite).
- (Deferred / out of scope) Publish nexmem-py to PyPI and nexmem-js to npm.

## Open risks (from BACKEND_RISKS.md)

- R-201: history rewrite COMPLETE (0 `db.*.supabase.co` matches in origin history).
- R-102: partial session revocation (token blocklist fails open on Redis outage).
- R-107: NetworkX graph single-worker only (enforced in render.yaml).
- R-301: Redis fail-open on auth / rate-limit paths (accepted for private beta).
