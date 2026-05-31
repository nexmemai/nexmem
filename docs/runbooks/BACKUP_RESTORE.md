# Runbook: Backup and restore

> **Scope.** How to take a manual backup of the production database, how to verify backup integrity, and how to perform a step-by-step restore. Covers the formal RTO / RPO targets for private beta and the documentation contract for the quarterly drill (P5-C10 / P9-G6).
>
> **Owner.** Operator (founder for the first private-beta cohort). The drill itself **must be performed by the operator** — Kiro can ship the documentation but cannot execute a real restore.
>
> **Related.** `POSTGRES_OUTAGE.md` (when to invoke a restore vs wait for upstream recovery), `docs/INCIDENT_RUNBOOK.md` (broader incident framing).

---

## 1. RTO / RPO targets for private beta

| Target | Value | Source |
|---|---|---|
| **RTO** (recovery time objective — how long the platform may be unavailable after a disaster) | **4 hours** | Private-beta SLO. To be re-evaluated before public beta. |
| **RPO** (recovery point objective — how much data may be lost) | **24 hours** | Aligned with Supabase Pro / Render Postgres "daily automated backup" cadence. |
| **Backup retention** | **30 days** rolling | Supabase Pro default; Render varies by plan — confirm in the dashboard. |
| **Drill cadence** | **Quarterly** | One restore drill per calendar quarter, time-boxed to 1 hour, results logged in `docs/incidents/<date>-backup-drill.md`. |

These targets are conservative for a memory-layer platform with no real-time-trading surface. They will be tightened when paying customers arrive.

---

## 2. What is backed up

| Layer | Storage | Backup mechanism | Recovery semantics |
|---|---|---|---|
| **PostgreSQL** (every user-scoped table — `users`, `api_keys`, `refresh_tokens`, `episodic_memory`, `semantic_memory`, `procedural_memory`, `knowledge_nodes`, `knowledge_edges`, `engrams`, `auth_audit_log`, `gdpr_audit_log`, `apps`, `token_usage`, plus `alembic_version`) | Supabase Pro **or** Render Postgres | Daily automated full backup + WAL-segment streaming on Pro plans (point-in-time restore window: 7 days on Supabase Pro). | Restore replaces the entire database. |
| **Redis** (rate-limit counters, brute-force lockout, monthly quotas, Celery broker queue, DLQ list, access-token blocklist) | Render Redis or Upstash | **No backup.** Redis state is intentionally transient — every entry is reconstructible from request traffic + Postgres-side audit logs. | Loss of Redis state does not require restore; it requires operator awareness of the carry-overs documented in `REDIS_OUTAGE.md`. |
| **Supabase / Render env vars** (`DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `OPENAI_API_KEY`, `SENTRY_DSN`, `READ_ONLY`, …) | Render dashboard | Documented in `docs/INCIDENT_RUNBOOK.md`; rotated values logged in 1Password. | Manually re-set during a fresh-environment restore. |
| **Object storage** (none currently) | n/a | n/a | n/a |

Anything not listed above does not need backup. The application itself is recreated by re-deploying from `main` on GitHub.

---

## 3. Trigger a manual backup

### 3.1 On Supabase

```
Dashboard → Project → Database → Backups → "Create backup now"
```

Backups appear in the same panel within 10–60 seconds. A manual backup is treated identically to an automated one for restore purposes.

### 3.2 On Render Postgres

```
Dashboard → Database → Backups → "Manual snapshot"
```

Render also runs an automatic daily backup at 04:00 UTC; the manual snapshot is in addition to that.

### 3.3 Local SQL dump (optional, off-platform copy)

```bash
# Run on a workstation that has the production DATABASE_URL.
pg_dump --format=custom --file=nexmem-$(date -u +%Y%m%dT%H%M%SZ).dump \
        --no-owner --no-privileges \
        "$DATABASE_URL"

# Verify the dump is well-formed.
pg_restore --list nexmem-*.dump | wc -l   # should show >100 entries
```

Encrypt before storing off-platform:

```bash
gpg --symmetric --cipher-algo AES256 nexmem-*.dump
shred -u nexmem-*.dump   # remove the unencrypted copy
```

---

## 4. Verify backup integrity

> Goal: prove the backup is restorable **before** an incident requires it.

### 4.1 Integrity check (Supabase / Render UI)

The dashboards report a SHA-256 checksum and a "verified" status. Confirm both are present for the most recent backup before considering it usable. **A backup that the platform has not verified is not a backup.**

### 4.2 Restore-to-staging dry run

This is the only real verification. Perform during the quarterly drill:

```bash
# 1. Provision a fresh, empty Postgres in your staging org (Supabase Free
#    is sufficient for the drill).
# 2. Get the staging DATABASE_URL.
export STAGING_DATABASE_URL="postgresql+asyncpg://postgres:...@db.staging.example:5432/postgres"

# 3. Convert to a non-asyncpg URL for psql / pg_restore.
export STAGING_PSQL_URL="${STAGING_DATABASE_URL/+asyncpg/}"

# 4. Restore from the most recent backup.
pg_restore --no-owner --no-privileges --dbname "$STAGING_PSQL_URL" nexmem-latest.dump

# 5. Verify schema head matches.
alembic -c alembic.ini current        # against STAGING_DATABASE_URL via env
# Expect the same head as production (currently 020_engrams_app_id).

# 6. Verify row counts within 1% of production.
psql "$STAGING_PSQL_URL" -c "
  SELECT
    (SELECT count(*) FROM users)              AS users,
    (SELECT count(*) FROM episodic_memory)    AS episodic,
    (SELECT count(*) FROM semantic_memory)    AS semantic,
    (SELECT count(*) FROM engrams)            AS engrams,
    (SELECT count(*) FROM auth_audit_log)     AS auth_audit,
    (SELECT count(*) FROM gdpr_audit_log)     AS gdpr_audit;
"

# 7. Confirm pgvector + extensions are present.
psql "$STAGING_PSQL_URL" -c "SELECT extname FROM pg_extension ORDER BY 1;"
# Expect at least: pgvector, pg_trgm, plpgsql.

# 8. Spin up a staging worker against this DB and run the standard
#    smoke test:
RUN_DB_TESTS=1 DATABASE_URL="$STAGING_DATABASE_URL" \
  pytest tests/test_isolation_and_write.py tests/test_app_scoping.py -v
```

If any step fails, the backup is **not** usable and the operator must escalate.

---

## 5. Step-by-step restore procedure

Use this only after declaring data loss in `POSTGRES_OUTAGE.md` Section 4.3.

### 5.1 Freeze writes

Set `READ_ONLY=true` on every running web replica (Render → Environment → deploy). Confirm `/health/live` is 200 and writes return 503.

### 5.2 Choose the restore point

| Loss type | Restore point |
|---|---|
| Schema corruption (bad migration, dropped table) | Most recent **backup before** the bad migration. |
| Data corruption (logical bug, accidental DELETE) | Most recent **point-in-time** ≤ 5 minutes before the corruption was committed. Supabase PITR is per-second; Render is per-snapshot (24-hour granularity). |
| Total infrastructure loss | Most recent verified daily backup. |

Document the chosen point in `docs/incidents/<date>-restore.md` before proceeding.

### 5.3 Execute the restore

#### On Supabase (point-in-time)

```
Dashboard → Database → Backups → "Restore to point in time"
→ Pick timestamp → Confirm
```

The restore creates a fresh Postgres instance with a new connection string. Keep both the old and new strings noted.

#### On Render Postgres

```
Dashboard → Database → Backups → "Restore from snapshot"
→ Pick snapshot → Restore creates a new database
```

#### From an off-platform `pg_dump`

```bash
# 1. Provision a new empty Postgres on Supabase / Render.
# 2. Decrypt the dump.
gpg --decrypt nexmem-<timestamp>.dump.gpg > restore.dump

# 3. Restore.
pg_restore --no-owner --no-privileges --dbname "$NEW_DATABASE_URL_PSQL" restore.dump
```

### 5.4 Re-point the application

Update `DATABASE_URL` in Render env vars to point at the restored instance. Trigger a deploy.

### 5.5 Run migrations

The restored database should already be on a known head. Sanity-check:

```bash
./scripts/run_with_migrations.sh
# alembic upgrade head, with the advisory lock — should be a no-op
```

---

## 6. Verify the restored database is correct

Run all of these. **Do not** clear `READ_ONLY=true` until every check passes.

### 6.1 Schema head

```bash
alembic current
# Expected: 020_engrams_app_id (or whatever head was on the day of the backup)
```

### 6.2 Row-count check vs pre-incident snapshot

Compare against the snapshot saved during incident response (per `POSTGRES_OUTAGE.md` Section 2.4).

```sql
SELECT 'users' AS table, count(*) FROM users
UNION ALL SELECT 'episodic_memory', count(*) FROM episodic_memory
UNION ALL SELECT 'semantic_memory', count(*) FROM semantic_memory
UNION ALL SELECT 'engrams', count(*) FROM engrams;
```

Acceptable delta: row counts within 1% **plus** the difference accounted for by the chosen RPO window.

### 6.3 RLS posture

```sql
SELECT relname,
       relrowsecurity AS rls_enabled,
       relforcerowsecurity AS rls_forced
FROM pg_class
WHERE relname IN (
  'users','api_keys','refresh_tokens','episodic_memory',
  'semantic_memory','procedural_memory','knowledge_nodes',
  'knowledge_edges','engrams','token_usage'
)
ORDER BY relname;
```

Every row should have `rls_enabled = true` and `rls_forced = true`. If any are false, the restore lost the RLS state — re-apply migrations 008 + 013 + 019 manually.

### 6.4 End-to-end smoke test

```bash
# Use a non-production credential.
curl -fsS -X POST https://nexmem-api.onrender.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@nexmem.example","password":"correct-password"}' \
  | jq .

# Followed by an episode write + recall.
```

Expected: HTTP 200 on every step, with non-empty `engram_id` from the write.

### 6.5 Re-enable writes

```
READ_ONLY=false
```

Trigger a final deploy. Verify `/health/ready` returns `200 ready`.

---

## 7. Quarterly drill — operator action

> **This is the formal P5-C10 / P9-G6 drill.** Time-box: 1 hour.

Schedule one drill per calendar quarter. The drill is non-negotiable for SOC2 readiness.

Procedure:

1. Pick a scratch date (avoid week-of-launch). Add a calendar block.
2. Provision a clean staging Postgres.
3. Execute Section 4.2 (restore-to-staging).
4. Execute Section 6.1–6.4 (verification, against the staging instance).
5. Tear down the staging Postgres.
6. Log results in `docs/incidents/<YYYY-QN>-backup-drill.md` with the schema:

   ```markdown
   # Backup restore drill — <YYYY-MM-DD>

   - Backup source: <Supabase project / Render DB> @ <timestamp>
   - Restore target: <staging cluster name>
   - Schema head matched: yes / no
   - Row counts within tolerance: yes / no
   - RLS posture preserved: yes / no
   - Smoke test passed: yes / no
   - Wall-clock time: <minutes>
   - Issues encountered: <none, or list>
   - RTO observation: <minutes from "start restore" to "smoke test passed">
   ```

If any check fails, file a follow-up issue tagged `reliability`. **A failed drill is itself an incident** — the next real disaster cannot be the first time the procedure is exercised.

---

## 8. What is **not** in scope for this runbook

- **Application redeploy.** Recreating the Render web/worker services from `main` is documented in `DEPLOY.md`, not here.
- **Secrets rotation.** Documented in `docs/INCIDENT_RUNBOOK.md` (Phase 2 deliverable).
- **Redis recovery.** See `REDIS_OUTAGE.md` — no restore needed; state is rebuilt from traffic + audit logs.
- **Git history rewrite.** A separate operator action (`docs/INCIDENT_RUNBOOK.md`) for the leaked-credential cleanup. Unrelated to data restore.
