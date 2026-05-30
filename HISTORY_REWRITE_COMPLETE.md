# Git History Rewrite — Completion Record

> **STATUS: COMPLETED.** This records a security-incident remediation that has
> been carried out and force-pushed. Items I could verify from the repo are
> checked; operator-only items (credential rotation in external dashboards) are
> left for the operator to confirm and are clearly marked PENDING. No secret
> values appear in this file.

---

## 1. Metadata

- **Date / time (UTC):** 2026-05-30 (force-push completed; exact timestamp per operator record)
- **Performed by:** operator (fresh-clone rewrite) + Kiro (verification & reconcile)
- **Fresh clone path used:** `C:\memorylayer-rewrite`
- **Rewrite tool:** `git-filter-repo` (`--replace-text replacements.txt --force`)
- **Remote rewritten:** `https://github.com/nexmemai/nexmem.git` (token-free)

---

## 2. Reason for rewrite

A Supabase database credential (password + project ref + pooler/host names)
from the Phase 1 incident, plus a GitHub PAT embedded in the `origin` remote
URL, were committed to / reachable in git history across multiple branches.
Rotating the credentials removed the live risk, but the old values were still
exfiltratable from history. This rewrite purged them from **all** historical
commits on **all** branches and tags.

---

## 3. What was removed from history (high level)

- [x] The leaked Supabase **password** (both URL-encoded and decoded forms)
- [x] The Supabase **project ref**
- [x] The Supabase **db host** (`db.<ref>.supabase.co`)
- [x] The Supabase **pooler hosts** (ap-south-1, ap-northeast-1)
- [x] The **GitHub PAT** (and the token-bearing remote URL that exposed it)
- Each replaced with a non-secret placeholder token.

---

## 4. Execution checklist

- [x] Fresh clone used (rewrite NOT run on the day-to-day working copy)
- [x] All local branches present before rewrite
- [x] `git-filter-repo --replace-text replacements.txt --force` completed without error
- [x] All branches rewritten (22 branches)
- [x] Tags rewritten
- [x] `origin` re-added **token-free** (`https://github.com/nexmemai/nexmem.git`)
- [x] Force-push of all branches completed
- [x] Force-push of all tags completed
- [x] Secret-marker count in rewritten history verified **= 0** before pushing
- [x] Scanner green after rewrite (`python scripts/scan_secrets.py` → clean, after the SHA-256 tripwire fix)
- [x] Test suite green after rewrite (264 passed / 0 failed / 33 skipped)
- [ ] Collaborators notified to delete + re-clone — **operator action**
- [x] Old local clone (`C:\memorylayer`) reconciled onto rewritten history

---

## 5. Post-rewrite operator checklist

- [ ] GitHub no longer reports the secret as exposed (Security → Secret scanning alerts)
- [ ] All expected PR branches still exist on origin (list below)
- [ ] Each PR's CI re-ran on the new SHAs and is green
- [ ] Supabase database password confirmed **rotated** (already done pre-rewrite)
- [ ] GitHub PAT confirmed **rotated** (any PAT pasted into chat also revoked)
- [ ] `SECRET_KEY` (JWT signing) rotated in Render — **PENDING**
- [ ] `ADMIN_API_KEY` set/rotated in Render — **PENDING**
- [ ] No tokenized remotes remain locally on any machine (`git remote -v` clean everywhere)
- [ ] Windows Credential Manager cleared of the old PAT, if it was cached

### PR branches expected on origin (tick once confirmed present post-push)

- [ ] `main`
- [ ] `master`
- [ ] `chore/p2-backend-hardening` (PR #3)
- [ ] `chore/p3-auth-hardening` (PR #5)
- [ ] `chore/p5-p6-p7-prod-hardening` (PR #6)
- [ ] `chore/p6-celery-hardening` (PR #7)
- [ ] `chore/before-public-beta-batch` (PR #8)
- [ ] `chore/before-billing-audit-logs` (PR #9)
- [ ] `chore/before-soc2-polish` (PR #10)
- [ ] `chore/before-soc2-batch-2` (PR #11)
- [ ] `chore/p4-apps-first-class` (PR #13)
- [ ] `chore/p7-rate-limits-error-hygiene` (PR #14)
- [ ] `chore/p9-reliability-incident-response` (PR #15)
- [ ] `chore/p8-observability` (PR #16)
- [ ] `chore/p3-auth-completion` (PR #17)
- [ ] `chore/p11-operator-tooling` (PR #18)
- [ ] `chore/p4-app-metrics` (PR #19)
- [ ] `chore/p12-sdk-publish` (PR #20)
- [ ] `docs/sdk-quickstarts` (PR #21)
- [ ] `backend/hardening-private-beta` (PR #1)
- [ ] `backend/hardening-phase2` (PR #2)
- [ ] `docs/backend-hardening-phase3-plan` (PR #4)
- [ ] `kiro/work-log` (PR #12)

---

## 6. Verification numbers

- Commits across all refs (local, incl. unreachable pre-rewrite objects): 382
- Real GitHub PAT (`ghp_` + 30+ chars) in rewritten remote history: **0** (9 `ghp_` mentions are the scanner regex + docs)
- Real OpenAI keys (`sk-` + 30+ chars, non-placeholder) in rewritten remote history: **0** (only `FAKE_OPENAI_KEY` in `CONTRIBUTING.md`)
- Supabase password / project ref in rewritten remote history: **0**
- Scanner result: **clean** (after SHA-256 tripwire fix)
- Test suite: **264 passed, 0 failed, 33 skipped** (5 deselected)

---

## 7. Notes / issues encountered

- The rewrite replaced the cleartext incident value inside
  `scripts/scan_secrets.py` (where it lived as a `re.compile(r"...")`
  tripwire). The placeholder was not valid regex, so the scanner crashed on
  import across all branches. Fixed by switching to a SHA-256 hash-based
  tripwire (commit `fix: restore secret-scanner tripwire after history
  rewrite`). This fix must be present on any branch before its CI can pass.
- The local `C:\memorylayer` clone still retains pre-rewrite objects as
  unreachable (e.g. via reflog), so `git log --all -p` there can still surface
  the old value; this is local-only and not present on origin. A fresh clone
  is clean.

---

## 8. Collaborator instructions (copy into the team channel)

After this rewrite, every collaborator MUST:

1. Stop work and do not push from any pre-rewrite clone.
2. Save any uncommitted work as a patch (`git diff > mywork.patch`).
3. Delete the old local clone.
4. Re-clone fresh from `https://github.com/nexmemai/nexmem.git`.
5. Reapply saved work onto the fresh clone.
6. Never reuse the old (rotated) credentials.
