# Nexmem — Post-Merge Status Snapshot (main)

## Section 1 — Headline

- Date (UTC): 2026-05-31
- Branch: `main`
- Tip SHA: `7a02202`
- Headline: **All hardening blocks (1–9 + history rewrite + Block 10–13 CI fixes) merged to main via PR #23; backend is green and ready for private beta — deployment/config and operator actions still pending.**

## Section 2 — Code & Test Status

- Python version: 3.11.9 (project `.venv`)
- Last light verification run (on `main` @ `7a02202`, this session):
  - app import (`import app`): **OK**
  - secret scan (`scripts/scan_secrets.py`): **clean** (no matches in tracked files)
  - pytest (`-m "not slow and not integration"`): **264 passed, 33 skipped, 5 deselected, 0 failed**
- No failures observed. Counts match the established post-merge baseline from the
  previous session (264 passed in the `tests/` CI scope); nothing new or regressed.
- Note on interpreter: checks were run with the project `.venv` Python, which has
  the backend dependencies installed. The system/global Python does not have them.

## Section 3 — CI / GitHub Status (from knowledge only)

- PR #23 (`chore/p12-sdk-publish` → `main`) is **merged**; `origin/main` is the
  merge commit `7a02202`. CI was green enough to allow the merge (the lint-and-test
  and CodeQL blockers were resolved on tip `15fab12` before merge — see
  `FINAL_MERGE_REPORT.md`).
- Backend CI jobs (integration-tests, alembic-roundtrip, dependency-audit) run on
  every PR (including docs PRs) so CI reflects real backend truth. CI DB jobs
  connect to the GitHub Postgres service with `DB_REQUIRE_SSL=false`; production
  keeps SSL required.
- **GitHub Actions status for `main` could not be observed from this environment;
  CI is assumed green as of the merge of PR #23.** (No `gh` CLI / API access here.)

## Section 3b — PR #24 (docs/post-merge-status) merge policy

- PR #24 carries the CI-truth fixes: valid `dependency-audit` command, the
  `DB_REQUIRE_SSL` toggle so `alembic-roundtrip` + `integration-tests` connect to
  the non-TLS CI Postgres, and backend jobs running on all PRs.
- **`dependency-audit` is expected to remain RED on PR #24.** PR #24 does not
  change `requirements.txt`, so the 12 pre-existing CVEs (7 packages) still
  report. This is NOT hidden or suppressed — the job runs and reports honestly.
  The CVE remediation lives on branch `chore/dependency-cve-upgrades` (see
  `DEPENDENCY_CVE_STATUS.md`).
- **Merge decision: BLOCKED pending policy confirmation.** Whether
  `dependency-audit` is a *required* status check is configured in GitHub
  branch-protection settings, which cannot be read from this environment (no
  `gh` / API; no CODEOWNERS or branch-protection-as-code in the repo).
  - If `dependency-audit` IS a required check → **do NOT merge PR #24** until the
    dependency PR lands (or the two are combined).
  - If `dependency-audit` is NON-blocking → PR #24 may be merged with the red
    `dependency-audit` documented here as a known, tracked exception.
- This file does not claim PR #24 is fully green; one security check is red by
  design until the dependency PR merges.

## Section 4 — Operator / Deployment TODOs

Human-only actions before real beta traffic (per `FINAL_MERGE_REPORT.md` and
`MERGE_READY.md` — referenced, not re-invented):

- Close superseded PRs #1–#22 (batch `gh pr close` command in `FINAL_MERGE_REPORT.md`).
- Configure Render production env vars: `DATABASE_URL` (sync:false, no embedded
  creds), `REDIS_URL`, `SECRET_KEY` (fresh value), `OPENAI_API_KEY`, `SENTRY_DSN`,
  `ADMIN_API_KEY`; ensure `DEMO_MODE` is unset.
- Run `alembic upgrade head` against the live DB via `scripts/run_with_migrations.sh`
  (target head `024_app_suspension`).
- Rotate `SECRET_KEY` once more to invalidate any pre-Phase-1 JWTs.
- Verify `/health/ready` returns 200 on the live URL after deploy.
- Ensure all collaborators delete pre-history-rewrite clones and re-clone.

## Section 5 — Next Technical Steps

- Render deployment + post-deploy smoke tests (`/health/live`, `/health/ready`,
  register → login → api-key → memory write/context round trip).
- Wire Celery Beat schedules for `execute_scheduled_deletions` (daily) and
  `enforce_data_retention` (weekly) — R-303 / R-304.
- Address open `BACKEND_RISKS.md` 300-series items before public launch
  (R-301 Redis fail-open, R-302 TOTP complete-login rate limit).
- (Deferred / separate decision) Phase 2+ backend hardening, SDK publishing to
  PyPI/npm. Not started in this session.

> Note: `CURRENT_STATUS.md` also exists and was refreshed post-merge in the
> previous commit on this branch. This file (`CURRENT_STATUS_MAIN.md`) is the
> focused main-branch post-merge snapshot in the structure requested for this
> session.
