# Nexmem — Current Status

- Date: 2026-05-31
- Branch: `chore/p12-sdk-publish` (PR #23 → `main`)
- Tip SHA: `76a9401`
- `origin/main` SHA: `c2f9212` (hardening stack NOT yet merged — see below)
- Total commits on branch history: 129
- Python: 3.11.9
- Migration head (offline chain): `024_app_suspension`

## Status: HARDENING STACK READY TO MERGE — MERGE PENDING CI + UI

The full hardening stack (Blocks 1–9 + history rewrite + Block 10–13 fixes)
lives on `chore/p12-sdk-publish` (PR #23) and is verified green locally. It is
NOT yet on `main` — `origin/main` is still `c2f9212`. The merge of PR #23 into
`main` must be done via the GitHub UI after CI is confirmed green (CI status
cannot be observed from this workspace; no `gh` CLI / API access).

## Verification (branch @ 76a9401)

- app imports: OK
- API key prefix: `nxm_`
- Secret scanner: clean (SHA-256 hash tripwire)
- Tests (CI-equivalent `-m "not slow and not integration"`): 279 passing, 0 failing, 33 skipped
- Tests (`tests/` only): 264 passing, 0 failing, 33 skipped (collection-scope baseline)
- flake8 blocking gate: 0 findings
- migration-lint (changed files): clean
- Merge into `main`: conflict-free (dry run)

## CI blockers fixed this session

- CodeQL "clear-text logging of sensitive information" in the quickstart
  examples — redacted all token/key/response-body logging (commit `76a9401`).
- (Earlier on branch: flake8 F821 import fixes, migration-lint annotations,
  timezone-aware JWT timestamps.)

## Open PRs

- #23 open (this branch → main), CI status to be confirmed in GitHub.
- #1–#22 open; to be closed as superseded once #23 merges (see FINAL_MERGE_REPORT.md).

## Remaining operator actions (see FINAL_MERGE_REPORT.md for full list)

1. Confirm PR #23 CI green; merge PR #23 via GitHub UI.
2. Close superseded PRs #1–#22.
3. Render env vars: DATABASE_URL, REDIS_URL, SECRET_KEY (fresh), SENTRY_DSN, ADMIN_API_KEY; DEMO_MODE unset.
4. Apply Alembic migrations on live DB (head 024_app_suspension).
5. Rotate SECRET_KEY to invalidate pre-rewrite JWTs; ensure collaborators re-clone.
6. (Deferred) Publish nexmem-py / nexmem-js when ready.
