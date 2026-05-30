# Merge Sequence Runbook (Post-Rewrite) — Merge the Tip Only

> **Context:** The git-history rewrite is **COMPLETE** (see
> `HISTORY_REWRITE_COMPLETE.md`). This runbook covers landing the full backend
> hardening stack on `main`. It does NOT instruct another rewrite. Do not run
> `git-filter-repo` again. Do not force-push `main`.

**Ground truth at write time (2026-05-30):**
- `origin/main` = `c2f9212` (post-rewrite)
- Tip branch `docs/sdk-quickstarts` (PR #21) = `93d797c` (post-rewrite, after
  the Block 11 scanner-fix cherry-pick)
- `chore/merge-prep` (PR #22) = tip + 3 Block 10 commits (scanner fix, tz fix,
  docs)

---

## ⚠️ Correction: the original linear 18-PR plan was WRONG

An earlier version of this runbook described a **linear 18-PR merge sequence**
(`#4 → #3 → #5 → … → #21`) with per-step migration checkpoints, on the
assumption that each PR branch was stacked on the previous block's tip.

**That assumption is incorrect.** Verified in Block 11:

- All 17 canonical branches **fork directly off `main`** (each branch's
  merge-base with `main` IS `origin/main`). They are **parallel**, not stacked.
- Their `ahead_of_main` counts increase monotonically (12 → 16 → … → 45), which
  means the work is **cumulative** and the tip branch
  `docs/sdk-quickstarts` (+45) already contains the entire stack.
- Merging the lower PRs **individually in sequence CONFLICTS**: a simulated
  `#3` then `#5` merge collides on the auth files (`app/core/security.py`,
  `app/core/deps.py`, `app/routers/auth.py`, `app/core/demo_auth.py`,
  `app/models/auth.py`). PR #5 (`chore/p3-auth-hardening`) and PR #17
  (`chore/p3-auth-completion`) are **two parallel auth lineages** — only one
  (the tip, via #17) should land.
- Merging **only the tip** into `main` is **clean** (verified with
  `git merge-tree --write-tree origin/main origin/docs/sdk-quickstarts`).

**Conclusion: merge PR #21 only. Close the rest as superseded.**

---

## The real strategy (one merge)

1. **Merge PR #21** (`docs/sdk-quickstarts` → `main`). This single merge lands
   the full cumulative hardening stack (Blocks 1–9) plus the SHA-256
   scanner-tripwire fix.
2. **Decide on PR #22** (`chore/merge-prep` → currently based on the tip). It
   adds the Block 10 docs + the timezone-aware `iat`/cutoff fix. See the
   "PR #22 decision" section below — this is a flagged decision, not an
   automatic merge.
3. **Close PRs #1–#20** as **superseded** (NOT merged). Their content is already
   in the tip.
4. Post-merge checks + branch hygiene.

---

## Stack health snapshot (Block 11, content-verified)

| Branch | PR | Ahead of main | Disposition |
|---|----|---------------|-------------|
| `docs/sdk-quickstarts` **(TIP)** | #21 | 45 | **MERGE this into main** |
| `chore/p12-sdk-publish` | #20 | 43 | close — content in tip |
| `chore/p4-app-metrics` | #19 | 41 | close — content in tip |
| `chore/p11-operator-tooling` | #18 | 39 | close — content in tip |
| `chore/p3-auth-completion` | #17 | 37 | close — content in tip (canonical auth) |
| `chore/p8-observability` | #16 | 35 | close — content in tip |
| `chore/p9-reliability-incident-response` | #15 | 33 | close — content in tip |
| `chore/p7-rate-limits-error-hygiene` | #14 | 30 | close — content in tip |
| `chore/p4-apps-first-class` | #13 | 26 | close — content in tip |
| `chore/before-soc2-batch-2` | #11 | 25 | close — content in tip |
| `chore/before-soc2-polish` | #10 | 24 | close — content in tip |
| `chore/before-billing-audit-logs` | #9 | 23 | close — content in tip |
| `chore/before-public-beta-batch` | #8 | 22 | close — content in tip |
| `chore/p6-celery-hardening` | #7 | 21 | close — content in tip |
| `chore/p5-p6-p7-prod-hardening` | #6 | 20 | close — content in tip |
| `chore/p3-auth-hardening` | #5 | 16 | close — **superseded auth lineage** |
| `chore/p2-backend-hardening` | #3 | 12 | close — content in tip |
| `backend/hardening-phase2` | #2 | — | close — superseded Phase-2 lineage |
| `backend/hardening-private-beta` | #1 | — | close — superseded by #3 |
| `docs/backend-hardening-phase3-plan` | #4 | — | docs-only — close or fold (see below) |
| `kiro/work-log` | #12 | — | log branch — close or retire (see below) |

"content in tip" = the branch's cumulative work is present in the tip; its own
commit SHAs differ post-rewrite, so it shows as ahead-of-main, but the work
landed in the tip.

---

## Step 1 — Pre-merge verification (PR #21)

All must hold before merging. STOP and resolve if any fail.

- [ ] Tip merges cleanly into main (re-confirm; should print a tree, exit 0):
  ```bash
  git fetch origin
  git merge-tree --write-tree origin/main origin/docs/sdk-quickstarts
  ```
  **STOP CONDITION:** any `CONFLICT` line, or non-zero exit.
- [ ] Scanner clean on the tip:
  ```bash
  git checkout -B verify-tip origin/docs/sdk-quickstarts
  python scripts/scan_secrets.py        # expect "clean", exit 0
  ```
  **STOP CONDITION:** any finding.
- [ ] Scanner-fix present on the tip (blob check):
  ```bash
  git rev-parse origin/docs/sdk-quickstarts:scripts/scan_secrets.py
  # expect 0d2c658edf7aaacf24a9ce9558192b5ccc1ce94f
  ```
- [ ] Test suite green on the tip:
  ```bash
  python -m pytest tests/ -x -q       # expect 264 passed / 0 failed / 33 skipped
  ```
  **STOP CONDITION:** any failure.
- [ ] CI green on PR #21 in GitHub Actions (lint-and-test, security-audit,
      docker-build, integration, secret-scan, migration-lint, CodeQL,
      pip-audit). **UNVERIFIED from the workspace — operator must confirm in the
      Actions tab.**
- [ ] Operator has given explicit **go/no-go = GO**.

---

## Step 2 — Merge PR #21 into main

Use a GitHub **merge commit** (not squash, not rebase) so the cumulative
history and the Block boundaries remain visible on `main`.

- Preferred: merge via the GitHub PR UI (PR #21, base `main`,
  head `docs/sdk-quickstarts`) → "Create a merge commit".
- CLI equivalent (only if doing it locally; still no force-push):
  ```bash
  git checkout main
  git pull --ff-only origin main
  git merge --no-ff origin/docs/sdk-quickstarts -m "Merge hardening stack (PR #21)"
  git push origin main
  ```
  **STOP CONDITION:** if `git merge` reports a conflict (it should not), abort
  with `git merge --abort` and report — do NOT hand-resolve blindly.

**Do NOT force-push main. Do NOT rewrite history.**

---

## Step 3 — Migrations (after #21 is on main)

The tip carries the full alembic chain. Run once, after the merge, against the
live DB via the advisory-locked wrapper:

```bash
scripts/run_with_migrations.sh        # wraps `alembic upgrade head` with an advisory lock
# target head after the merge = 024_app_suspension
```

**STOP CONDITION:** if `alembic upgrade head` errors, or the head is not
`024_app_suspension`, halt and investigate before announcing.

> Note: because we merge the tip once (not 18 PRs), there is a SINGLE migration
> checkpoint — not the per-PR checkpoints the old linear plan described.

---

## Step 4 — Post-merge verification

1. **Tests on main:**
   ```bash
   git checkout main && git pull --ff-only origin main
   python -m pytest tests/ -x -q
   ```
   STOP CONDITION: any failure.
2. **Secret scan on main:**
   ```bash
   python scripts/scan_secrets.py       # expect "clean", exit 0
   ```
   STOP CONDITION: any finding.
3. **Health checks (deployed service):**
   ```bash
   curl -fsS https://<live-host>/health/live      # expect HTTP 200
   curl -fsS https://<live-host>/health/ready     # expect HTTP 200
   ```
   STOP CONDITION: a non-200 from `/health/ready` means a dependency (DB/Redis)
   is not wired — do not announce beta.

---

## Step 5 — PR #22 decision (Block 10 docs + tz fix)

PR #22 (`chore/merge-prep`) is based on the tip and adds: the Block 10 docs
(`CURRENT_STATUS.md`, this runbook, risk-register updates, etc.) and a
timezone-aware `iat`/cutoff fix in `app/core/security.py` + `app/routers/admin.py`.

Choose ONE (operator decision — this runbook does not auto-merge it):

- **(a) Merge #22 right after #21.** After #21 lands, retarget PR #22's base to
  `main` (GitHub does this automatically once the tip is merged) and merge it.
  It should be a clean fast-forward-ish merge (tip + 3 commits).
- **(b) Fold #22 into #21 first**, then merge #21 only and close #22.
- **(c) Defer #22** to a later decision; land #21 alone for now.

**STOP CONDITION:** do not merge #22 until the operator picks an option.

---

## Step 6 — Close superseded PRs (do NOT merge them)

After #21 (and the #22 decision) is on `main`, close the rest. Their content is
already in the tip; merging them would double-apply and conflict.

Close as **superseded** with a short comment pointing at the #21 merge commit:

- **#1** `backend/hardening-private-beta` — superseded by the canonical stack.
- **#2** `backend/hardening-phase2` — superseded Phase-2 lineage (parallel).
- **#3, #6–#11, #13–#16, #18–#20** — content cumulative in the tip (#21).
- **#5** `chore/p3-auth-hardening` — **superseded auth lineage**; the auth work
  that landed is via #17, already in the tip.
- **#17** `chore/p3-auth-completion` — content in tip.
- **#4** `docs/backend-hardening-phase3-plan` — docs-only planning doc. Either
  merge it standalone (clean, low risk) or close if the plan is now captured
  elsewhere. Operator choice.
- **#12** `kiro/work-log` — historical log branch. Merge to keep the log on
  `main`, or formally retire. Operator choice.

Suggested close comment:
> Superseded by PR #21 (`docs/sdk-quickstarts`), which contains the full
> cumulative hardening stack. Closing without merge per
> `MERGE_SEQUENCE_RUNBOOK.md`. No content is lost.

---

## Step 7 — Branch hygiene (optional, after PRs closed)

Once #21 is merged and the superseded PRs are closed, the parallel feature
branches can be deleted to prevent anyone re-opening the old linear plan.
Delete remote branches only after confirming `main` is green:

```bash
# Example — delete one merged/superseded remote branch (NOT main/master):
git push origin --delete chore/p3-auth-hardening
```

- Keep `main` and `master` (operator decides on `master`'s fate separately).
- Consider keeping `kiro/work-log` until its content is preserved on `main`.
- **STOP CONDITION:** never delete a branch whose work you have not confirmed is
  on `main`. When in doubt, leave it.

> This runbook never force-pushes and never deletes `main`/`master`.

---

## DO NOT

- ❌ **Do NOT merge any lower-numbered hardening PR (#1–#20) after #21 merges.**
  Their cumulative content is already in the tip. Merging them double-applies
  overlapping changes and WILL conflict (proven: `#3` then `#5` collides on the
  auth files).
- ❌ Do NOT follow the old linear "18-PR sequence" — it was based on an
  incorrect stacked-branch assumption. The branches are parallel off `main`.
- ❌ Do NOT merge BOTH auth lineages. `#5` (`chore/p3-auth-hardening`) and `#17`
  (`chore/p3-auth-completion`) overlap; only the tip's lineage lands.
- ❌ Do NOT run `git-filter-repo` or any history rewrite (already done).
- ❌ Do NOT force-push `main` or `master`.
- ❌ Do NOT delete a branch before confirming its work is on `main`.

---

## Why the branches are parallel (structural note for contributors)

The pre-merge history rewrite (git-filter-repo) rebuilt commit objects across
every branch. The canonical hardening branches were each created from `main`
and carry a **cumulative** snapshot of all prior blocks, so each later branch is
a superset of the earlier ones. The newest branch, `docs/sdk-quickstarts`
(PR #21), therefore contains the whole stack. Treat the tip as the single
integration branch; the lower-numbered PRs are historical checkpoints, not
independently-mergeable units.

**Contributor guidance going forward:**
- Branch new work off `main` AFTER PR #21 is merged.
- Do not resurrect or re-open the closed hardening PRs.
- If you cloned before the rewrite, delete the clone and re-clone fresh.

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
2. Do NOT force-push `main` or `master`.
3. Do NOT merge anything to `main` without an explicit operator go/no-go.
4. Merge the **tip only** (PR #21); close the rest as superseded.
