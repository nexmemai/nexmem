# Operator Incident Runbook

**Last updated:** 2026-05-22 (Phase 2 hardening branch).

This runbook is the source of truth for operator actions when a secret
is leaked, when credentials must be rotated, or when stale credentials
must be purged from git history.

The Phase 2 hardening pass (`chore/p2-backend-hardening`) removes the
known leaked Supabase password from the working tree. This runbook
describes the **operator-only** steps that the agent could not perform
autonomously: history rewrite, force-push, collaborator notification,
and verification.

---

## 1. Rotate the Supabase database password

1. Open the Supabase dashboard for the affected project.
2. Project settings → Database → Connection string → **Reset password**.
3. Copy the new password to a password manager. Do not paste it into
   any file under `git`.
4. In the Render dashboard:
   - `nexmem-api` → Environment → set `DATABASE_URL` to the new
     pooler URL (port 6543, sslmode=require).
   - `nexmem-celery-worker` → Environment → set the same `DATABASE_URL`.
5. Redeploy both services. Verify `/health/ready` returns 200 with
   `database: ok` after the new revision is live.

The new pooler URL must use the `aws-1-...pooler.supabase.com:6543`
hostname (transaction-mode pooler). Direct database hostnames cause
asyncpg connection failures from Render's IPv4-only egress.

## 2. Rotate the Supabase service-role key

If a service-role key was committed:

1. Supabase dashboard → Project settings → API → **Reset
   service_role key**.
2. Update the new key in any process that needs it. The current
   backend does not use the service-role key; only update
   `nexmem-landing` if a Vercel env var still references it.
3. The old key is invalid as soon as the reset completes.

## 3. Rewrite git history to purge a leaked secret

Required when a credential has appeared in any tracked file in any
past commit, even if the working tree is now clean.

### 3a. Using `git filter-repo` (preferred)

```bash
# Install once
pip install git-filter-repo

# Mirror clone (operate on a fresh copy, not your working clone)
git clone --mirror git@github.com:nexmemai/nexmem.git nexmem-mirror.git
cd nexmem-mirror.git

# Provide the literal strings to scrub. One per line. The scrubber
# replaces each occurrence with the literal text "REMOVED".
cat > replacements.txt <<'EOF'
***REDACTED_PASSWORD***==>REMOVED
***REDACTED_PROJECT_ID***==>REMOVED
EOF

git filter-repo --replace-text replacements.txt
```

Verify nothing remains:

```bash
git log -p --all | grep -F 'Doesitmatter' && echo "STILL PRESENT" || echo "CLEAN"
git log -p --all | grep -F '***REDACTED_PROJECT_ID***' && echo "STILL PRESENT" || echo "CLEAN"
```

### 3b. Using BFG Repo-Cleaner (alternative)

```bash
java -jar bfg.jar --replace-text replacements.txt nexmem-mirror.git
cd nexmem-mirror.git
git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

### 3c. Force-push the cleaned history

```bash
# Push every branch and tag from the mirror
git push --force --mirror git@github.com:nexmemai/nexmem.git
```

This rewrites every branch and tag on the remote. Coordinate with
collaborators before doing this.

## 4. Notify collaborators

Once history has been rewritten, every collaborator must re-clone:

```bash
# Save any local work first
git status
git diff > /tmp/local-changes.patch

# Re-clone
cd ..
mv nexmem nexmem-old
git clone git@github.com:nexmemai/nexmem.git
```

Send a message to the team channel describing the action, the time of
the rewrite, and the commit hashes of any branches that were dropped.

## 5. Verify the secret is gone everywhere

After history rewrite:

```bash
git log -p --all -S "Doesitmatter" -- '*'
git log -p --all -S "***REDACTED_PROJECT_ID***" -- '*'
```

Both must return no output.

Also run the scanner:

```bash
python scripts/scan_secrets.py
```

The scanner is wired into CI; if it ever finds a match in a future PR,
that PR is blocked.

## 6. Verify all services use the new credentials

1. Render dashboard:
   - `nexmem-api` → Logs → look for `[run_with_migrations] running
     alembic upgrade head` followed by no SQL errors and a successful
     uvicorn boot.
   - `/health/ready` returns `{"status": "ready", "checks": {"database":
     "ok", "redis": "ok"}}`.
2. Manually exercise an authenticated route and watch the logs for a
   structured JSON line containing `request_id` and `user_id`.
3. From a clean machine:
   ```bash
   curl https://nexmem.onrender.com/health/ready
   ```
   should return 200.

---

## 7. Common operator commands

### Manually run migrations

Migrations are normally applied at startup by
`scripts/run_with_migrations.sh`, but you can force a one-shot run from
your laptop:

```bash
DATABASE_URL='postgresql://postgres.<ref>:<password>@<host>:6543/postgres' \
    alembic upgrade head
```

`alembic/env.py` acquires a Postgres advisory lock before applying any
migrations, so it is safe to run from multiple shells; only one will
succeed.

### Roll back a migration

```bash
DATABASE_URL='...' alembic downgrade -1
```

Migration `007_standardize_vector_dim` is destructive (it issues
`DELETE FROM semantic_memory`). Do not re-run it on the production
database. See R-205 in `BACKEND_RISKS.md`.

### Inspect quota usage in Redis

```bash
redis-cli --tls -u "$REDIS_URL" KEYS 'quota:*'
redis-cli --tls -u "$REDIS_URL" GET 'quota:write:<user_uuid>:2026-05'
```

### Disable a leaked API key

```sql
UPDATE api_keys SET is_active = false WHERE id = '<key_id>';
```

The key is invalidated on the next request because
`get_current_user` checks `is_active`.

### Rotate `SECRET_KEY`

Rotating the JWT signing secret invalidates every issued access and
refresh token immediately:

1. Generate a new value:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
2. Set it as `SECRET_KEY` in the Render dashboard.
3. Redeploy. All clients are forced to re-authenticate.
