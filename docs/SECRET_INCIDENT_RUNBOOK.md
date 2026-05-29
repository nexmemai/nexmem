# Secret Incident Runbook

**When to use this runbook:** any time a secret (database password,
API key, SSH key, OAuth secret, signing key) has been committed,
pushed, or otherwise exposed to a place outside its intended secrets
manager — even briefly.

This runbook covers the *only* secret incident the project has on
record so far: the Supabase database password
that was committed in `alembic/env.py` and several scripts before
Phase 1 of the backend hardening. Phase 1 removed every literal from
HEAD; this runbook is the operator-side completion of the incident.

> **Code cannot rotate provider-managed credentials.** Everything
> below requires a human with the right console / CLI access.

---

## 0. Pre-flight

- [ ] You are an authorised Supabase admin for the affected project.
- [ ] You can reach the GitHub repo as an admin / maintainer.
- [ ] You have a way to update Render env vars (or the equivalent
      production secret store).
- [ ] You have notified collaborators that a force-push is imminent.

---

## 1. Confirm the exposure

```bash
# In a fresh clone — never on your working repo.
git clone --mirror git@github.com:nexmemai/nexmem.git nexmem.audit.git
cd nexmem.audit.git
git log --all -p -S 'Doesitmatter' -- '*' | head -200
git log --all -p -S '***REDACTED_PROJECT_ID***' -- '*' | head -200
```

Record every commit SHA that touched the literal. These will become
the targets of the history rewrite.

---

## 2. Rotate provider-side first

Always rotate before scrubbing history. Until rotation is complete,
the credential remains usable from anyone who already cloned the repo.

### 2.1 Supabase database password

1. Supabase dashboard → Project settings → Database → "Reset database
   password".
2. Generate a strong random password
   (`python -c "import secrets; print(secrets.token_urlsafe(40))"`).
3. Update the new password in:
   - Render web service env var `DATABASE_URL`.
   - Render worker service env var `DATABASE_URL`.
   - Any developer `.env.local` (do not commit).
   - Any third-party tool (Metabase, BI dashboards, ETL jobs).

### 2.2 Supabase service-role key (defensive)

Service role bypasses RLS. If there is *any* doubt about whether it
was exposed (e.g. a contractor's laptop, an old `.env`):

1. Supabase dashboard → Project settings → API → "Reset service role
   key".
2. Update wherever it is referenced (server env vars, edge functions,
   CI secrets).

### 2.3 Other dependent secrets

Rotate or invalidate:

- `SECRET_KEY` (JWT signing) — recommended at the same time, since
  any pre-incident JWTs minted with the old secret are now suspect.
  Application restart with the new value invalidates all outstanding
  access *and* refresh tokens.
- `METRICS_SECRET_KEY` if it ever shared a process with the leaked
  password.
- Any Sentry DSNs, OpenAI keys, or other tokens that lived in the
  same `.env` file as the leaked password.

---

## 3. Scrub git history

Choose `git-filter-repo` (preferred) or BFG. Do this on a fresh
mirror clone, never on a developer's working clone.

### 3.1 `git-filter-repo`

```bash
# In a fresh clone:
git clone --mirror git@github.com:nexmemai/nexmem.git nexmem.scrub.git
cd nexmem.scrub.git

# Replace literals globally with a placeholder marker.
git filter-repo --replace-text <(cat <<'EOF'
***REDACTED_PASSWORD***==>***REMOVED***
***REDACTED_PASSWORD***==>***REMOVED***
***REDACTED_PROJECT_ID***==>***REMOVED***
EOF
)

# Inspect that the literals are gone from history.
git log --all -p -S 'Doesitmatter' -- '*' || echo "clean"
```

### 3.2 Force-push the rewritten history

> **DESTRUCTIVE.** Coordinate with collaborators first.

```bash
# Push every ref. --mirror requires repo settings to allow it.
git push --force --mirror git@github.com:nexmemai/nexmem.git
```

If branch protection blocks force-push, temporarily disable it,
push, then re-enable.

### 3.3 Tell GitHub to expire cached views

GitHub keeps cached views of old commits / forks for some time.
Open a support request:

> "We rewrote git history to remove a leaked credential. Please
> expire cached pull-request diffs and disable forks of any
> pre-rotation commits."

(GitHub's process: <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository>)

### 3.4 Notify collaborators

Send a brief notice to every contributor:

> Subject: nexmem: history rewrite, please re-clone
>
> We force-pushed a rewritten history to remove a leaked
> credential. Your local clone is now divergent from origin.
>
> Easiest fix: delete and re-clone.
>
> If you have local work to preserve, see `git-filter-repo`'s
> "After the rewrite" docs and rebase your branches onto the
> new HEAD.

---

## 4. Rebuild trust

- [ ] Confirm the secret scanner CI job is green on `main`. The
      job runs `python scripts/scan_secrets.py` and the
      `tests/test_scan_secrets.py::test_repo_is_clean` test on
      every PR.
- [ ] Confirm Supabase audit logs show no anomalous activity from
      pre-rotation timestamps.
- [ ] Add a calendar reminder to re-rotate the database password in
      90 days even if no further incident occurs.

---

## 5. Tooling reference

| Tool | Purpose | Install |
|---|---|---|
| `scripts/scan_secrets.py` | Repo-wide pattern scan; runs in CI. | bundled |
| `git-filter-repo` | Recommended history rewriter. | `pip install git-filter-repo` |
| `bfg` | Alternative history rewriter (Java). | <https://rtyley.github.io/bfg-repo-cleaner/> |
| `gitleaks` | Optional pre-commit hook (alternative scanner). | <https://github.com/gitleaks/gitleaks> |
| `trufflehog` | Optional entropy-based secret discovery. | <https://github.com/trufflesecurity/trufflehog> |

---

## 6. Pre-commit hook (optional, recommended)

Drop this into `.git/hooks/pre-commit` and `chmod +x` it:

```bash
#!/usr/bin/env bash
set -euo pipefail
python "$(git rev-parse --show-toplevel)/scripts/scan_secrets.py" --quiet
```

Or use the project's existing CI pattern via `pre-commit`:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: scan-secrets
        name: scan-secrets
        entry: python scripts/scan_secrets.py --quiet
        language: system
        pass_filenames: false
```

---

## 7. What this runbook does NOT cover

- Recovery from a confirmed *use* of the leaked credential.
  Post-rotation, audit Supabase logs for any access from unknown
  IPs and assume worst-case data exposure for the affected window.
- Customer notification. If the affected database held any
  customer data, consult legal and your DPA / privacy policy
  obligations.
- The non-Supabase cases (AWS, OpenAI, Stripe, GitHub PATs, etc.).
  Each provider has its own rotation flow; the high-level steps
  here transfer.
