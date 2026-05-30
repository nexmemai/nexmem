# Merge Sequence Runbook (Post-Rewrite)

> **Context:** The git-history rewrite is **COMPLETE** (see
> `HISTORY_REWRITE_COMPLETE.md`). This runbook covers merging the hardening
> stack into `main`. It does NOT instruct another rewrite. Do not run
> `git-filter-repo` again. Do not force-push `main`.

**Ground truth at write time (2026-05-30):**
- `origin/main` = `c2f9212` (post-rewrite)
- Tip branch `docs/sdk-quickstarts` = `7e0feb8` (post-rewrite)
- `chore/merge-prep` = `7e0feb8` + 3 Block 10 commits (scanner fix, tz fix, docs)

---

## Prerequisites (all must hold before ANY merge)

- [ ] Secret scanner clean on the branch being merged: `python scripts/scan_secrets.py` → exit 0.
- [ ] **Scanner-fix commit present** on the branch. The rewrite broke
      `scripts/scan_secrets.py`; a branch without the SHA-256 tripwire fix will
      fail CI on import. Cherry-pick / rebase it in first if missing.
- [ ] CI green on the branch (GitHub Actions): lint-and-test, security-audit,
      docker-build, integration, secret-scan, migration-lint, CodeQL, pip-audit.
- [ ] Working tree clean; no stash needed for the merge.
- [ ] Operator has given explicit **go/no-go = GO** for the merge sequence.

**STOP CONDITION:** if any prerequisite is false, stop and resolve before merging.

---

## Merge order (base of the whole stack = `main`)

The stack is linear; each PR was stacked on the previous block tip. Merge in
this exact order. Use `--no-ff` merges (or GitHub "Create a merge commit") so
the stack boundaries stay visible.

| # | PR | Branch | Notes |
|---|----|--------|-------|
| 1 | #4 | `docs/backend-hardening-phase3-plan` | docs-only; safe to merge first |
| 2 | #3 | `chore/p2-backend-hardening` | **run migrations after** (see below) |
| 3 | #5 | `chore/p3-auth-hardening` | |
| 4 | #6 | `chore/p5-p6-p7-prod-hardening` | |
| 5 | #7 | `chore/p6-celery-hardening` | |
| 6 | #8 | `chore/before-public-beta-batch` | |
| 7 | #9 | `chore/before-billing-audit-logs` | |
| 8 | #10 | `chore/before-soc2-polish` | |
| 9 | #11 | `chore/before-soc2-batch-2` | |
| 10 | #13 | `chore/p4-apps-first-class` | **run migrations after** (apps table) |
| 11 | #14 | `chore/p7-rate-limits-error-hygiene` | |
| 12 | #15 | `chore/p9-reliability-incident-response` | |
| 13 | #16 | `chore/p8-observability` | |
| 14 | #17 | `chore/p3-auth-completion` | migration 021/022 |
| 15 | #18 | `chore/p11-operator-tooling` | |
| 16 | #19 | `chore/p4-app-metrics` | migration 023/024 |
| 17 | #20 | `chore/p12-sdk-publish` | |
| 18 | #21 | `docs/sdk-quickstarts` | tip; Block 9 docs |

Side PRs: **#1** and **#2** (superseded by #3) — close WITHOUT merge after #3
lands. **#12** (`kiro/work-log`) — merge last or formally retire.

**STOP CONDITION (each row):** do not advance to the next PR until the current
PR's CI is green on `main` after merge.

---

## Migration checkpoints

Run AFTER PR #3 merges, and again AFTER PR #13/#17/#19 merge (any PR that adds
an alembic revision). Use the advisory-locked wrapper:

```bash
scripts/run_with_migrations.sh    # wraps `alembic upgrade head` with an advisory lock
# target head after the full stack = 024_app_suspension
```

**STOP CONDITION:** if `alembic upgrade head` errors or the head is not the
expected revision, halt the sequence and investigate before merging further.

---

## Post-merge verification

After the full stack is on `main`:

1. **Tests:**
   ```bash
   pytest tests/ -x -q
   ```
   STOP CONDITION: any failure halts the rollout.

2. **Health checks (against the deployed service):**
   ```bash
   curl -fsS https://<live-host>/health/live    # expect HTTP 200
   curl -fsS https://<live-host>/health/ready    # expect HTTP 200
   ```
   STOP CONDITION: a non-200 from `/health/ready` means a dependency (DB/Redis)
   is not wired — do not announce beta.

3. **Secret scan on `main`:**
   ```bash
   python scripts/scan_secrets.py    # expect "clean", exit 0
   ```
   STOP CONDITION: any finding halts the rollout.

---

## Operator-owned items (NOT done by this runbook)

- Render env vars: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` (≥32 hex),
  `OPENAI_API_KEY`, `SENTRY_DSN`, `ADMIN_API_KEY`, `ENVIRONMENT=production`;
  ensure `DEMO_MODE` is unset.
- Rotate `SECRET_KEY` and `ADMIN_API_KEY` in Render.
- Celery Beat schedules for `execute_scheduled_deletions` (daily) and
  `enforce_data_retention` (weekly) — R-303 / R-304.
- Tell collaborators to delete old clones and re-clone (post-rewrite).

---

## Hard rules for this runbook

1. Do NOT run `git-filter-repo` or any history rewrite (already done).
2. Do NOT force-push `main`.
3. Do NOT merge anything to `main` without an explicit operator go/no-go.
4. One PR at a time; CI must be green before advancing.
