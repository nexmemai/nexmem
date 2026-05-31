# Runbook: Postgres outage

> **Scope.** What to do when the application's primary PostgreSQL database (Supabase or Render Postgres) is unreachable, slow, or rejecting connections. Covers full outages, partial degradation (high latency, pool exhaustion), and recovery verification.
>
> **Owner.** Backend on-call. Escalate per Section 6 if the DB does not recover within 30 minutes.
>
> **Related runbooks.** `BACKUP_RESTORE.md` (data-loss recovery), `REDIS_OUTAGE.md` (rate-limit / quota fallout), `OPENAI_OUTAGE.md` (independent dependency).

---

## 1. Symptoms

What end-users / operators see:

- API returns **HTTP 503 `{"status":"degraded"}`** from `GET /health/ready`. The `database` field is either `error: ...` or `database_latency_ms` exceeds 1000.
- API write routes return **HTTP 500** with a generic error (P7-E9 sanitises internals — check Render / Sentry logs for the underlying `OperationalError`, `ConnectionRefusedError`, or `asyncio.TimeoutError`).
- API read routes that touch the DB return 500 or hang up to the per-statement timeout (`statement_timeout=30s`, see `app/database.py` and `settings.db_statement_timeout_ms`).
- Celery worker logs show `consolidate_user_memory_task` retrying with `OperationalError` / `connection failed`.
- Sentry: spike in `OperationalError`, `DBAPIError`, or `ConnectionRefusedError` with a single common stack-frame in `app/database.py`.
- Render dashboard: web service is `live` but `unhealthy` because `/health/ready` is returning 503.

`GET /health/live` will continue to return 200 — that endpoint deliberately does **no** dependency probe (see `app/routers/health.py`).

---

## 2. Immediate actions (first 5 minutes)

### 2.1 Confirm the outage is real

Run from your laptop or a CI shell:

```bash
curl -fsS https://nexmem-api.onrender.com/health/ready | jq .
```

You should see `checks.database` set to either `"ok"` or an error string. If `database_latency_ms` is over 1000, the DB is up but slow.

If `/health/ready` itself times out, the web service is not just degraded — it is down. Skip ahead to Section 4.

### 2.2 Flip the read-only kill switch

> Goal: stop writes from corrupting state or piling up in flight while the DB recovers.

In Render dashboard → Web Service → Environment, set:

```
READ_ONLY=true
```

…then trigger a manual deploy (or restart the worker) so the process picks it up. With this set, `app/middleware/read_only_mode.py` returns **HTTP 503 with `Retry-After: 60`** for every state-changing route. Reads, `/health/*`, `/metrics`, and session-revocation endpoints (`/auth/logout`, `/auth/logout-all`, `DELETE /auth/sessions/{id}`) continue to flow.

### 2.3 Notify users

Post to the status page (or the user-facing channel of your choice). Suggested message:

> We are aware of database degradation affecting writes on the Nexmem API. Reads and authentication are operational. We will update once writes are restored.

### 2.4 Snapshot evidence

Before changing anything else, capture:

```bash
# From a workstation with Postgres credentials (use the read-only DB user
# if you have one).
psql "$DATABASE_URL_READONLY" -c "SELECT now(), version();"
psql "$DATABASE_URL_READONLY" -c "SELECT count(*) FROM pg_stat_activity;"
psql "$DATABASE_URL_READONLY" -c "SELECT state, wait_event_type, wait_event, count(*) FROM pg_stat_activity GROUP BY 1,2,3 ORDER BY 4 DESC;"
```

The `pg_stat_activity` snapshot is the single most useful artifact for post-incident analysis.

---

## 3. Diagnosis tree

### 3.1 Is the DB process alive?

```bash
# Supabase: check the project status page.
# Render Postgres: check the database's Service Status in the dashboard.
```

If the upstream is reporting an incident, **stop trying to fix it from the application side** — wait for the upstream and proceed to Section 5 once they declare resolution.

### 3.2 Is the connection pool exhausted?

`pool_size=5`, `max_overflow=5` per worker (see `settings.db_pool_size`, `settings.db_max_overflow`). With one worker per replica, that is a maximum of 10 connections from each web replica, plus 1 connection per Celery worker.

If the pool is exhausted but the DB itself is healthy, requests will queue for `pool_timeout=30s` and then raise `TimeoutError` from SQLAlchemy. Symptoms:

- `pg_stat_activity` shows many `idle in transaction` rows from `application_name='nexmem-production'`.
- Render request logs show 30s+ latency on previously-fast routes.

Mitigation:

1. Restart the web service (Render dashboard → Manual Deploy → Clear build cache and deploy). This force-closes every pooled connection.
2. If exhaustion recurs immediately, an idle-in-transaction leak is in flight. The Phase 2 hardening sets `idle_in_transaction_session_timeout=60s` on every connection (`settings.db_idle_in_transaction_timeout_ms`), which kills the offending session server-side. Look in Sentry for the request that opened the transaction.

### 3.3 Is a single query stuck?

Every connection sets `statement_timeout=30s`, so an individual query should self-cancel. If `pg_stat_activity` shows a query running >30s, the timeout was either not applied (check `application_name='nexmem-...'` is present) or the query was issued by a non-app client (a manual `psql` session, a migration, etc).

Find and cancel:

```sql
-- List queries running longer than 30s.
SELECT pid, now() - query_start AS runtime, state, application_name, left(query, 200) AS query
FROM pg_stat_activity
WHERE state != 'idle'
  AND now() - query_start > interval '30 seconds'
ORDER BY runtime DESC;

-- Cancel a specific pid (graceful):
SELECT pg_cancel_backend(<pid>);

-- Force-terminate if cancel does not work after 30s:
SELECT pg_terminate_backend(<pid>);
```

### 3.4 Is the DB out of disk / connections / shared buffers?

```sql
-- Free disk (Supabase Pro: 8 GB Pro tier; alert at 80%).
SELECT pg_size_pretty(pg_database_size(current_database()));

-- Connections vs limit.
SELECT count(*) AS used,
       (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max
FROM pg_stat_activity;

-- Largest tables.
SELECT relname, pg_size_pretty(pg_total_relation_size(oid)) AS size
FROM pg_class
WHERE relkind = 'r'
ORDER BY pg_total_relation_size(oid) DESC
LIMIT 10;
```

If disk is exhausted, reach out to Supabase / Render support to enlarge the volume; this is **not** a thing the application can self-heal.

---

## 4. Recovery procedure

Run these in order. After each step, re-check `/health/ready`.

### 4.1 Verify connectivity

```bash
psql "$DATABASE_URL" -c "SELECT 1;"
```

If this fails: the DB itself is still down. Stay in incident mode.

### 4.2 Verify schema is at expected head

```bash
# From a workstation with the production DATABASE_URL.
alembic current
```

Expected head as of this branch: **`020_engrams_app_id`** (run `alembic heads` to confirm). If the DB is on a stale head, the application will return 500s on routes that touch newly-added columns. Apply migrations through the advisory-lock wrapper:

```bash
./scripts/run_with_migrations.sh
```

That wrapper acquires `pg_try_advisory_lock(728_419_362_001)` so multi-replica deploys cannot race. The first replica wins; the rest see `false` and exit cleanly.

### 4.3 Confirm no data loss

```sql
-- Sanity counts on user-scoped tables. Compare against the
-- post-incident sample preserved in Section 2.4.
SELECT 'users' AS table, count(*) FROM users
UNION ALL SELECT 'episodic_memory', count(*) FROM episodic_memory
UNION ALL SELECT 'semantic_memory', count(*) FROM semantic_memory
UNION ALL SELECT 'procedural_memory', count(*) FROM procedural_memory
UNION ALL SELECT 'engrams', count(*) FROM engrams
UNION ALL SELECT 'audit_log', count(*) FROM auth_audit_log
UNION ALL SELECT 'gdpr_log', count(*) FROM gdpr_audit_log;
```

If counts are materially lower than the pre-incident snapshot, escalate per Section 6 and follow `BACKUP_RESTORE.md`. **Do not** flip read-only off until the data-loss question is answered.

### 4.4 Re-enable writes

```
READ_ONLY=false
```

Trigger a deploy. Then within ~60 seconds:

```bash
curl -fsS https://nexmem-api.onrender.com/health/ready | jq .
# All checks should be "ok" except those documented as "skipped".
```

### 4.5 Test a real write end-to-end

```bash
# Use a non-production demo user.
curl -fsS -X POST https://nexmem-api.onrender.com/api/v1/memory/episode/write \
  -H "Authorization: Bearer $TEST_USER_JWT" \
  -H "Content-Type: application/json" \
  -d '{"content":"runbook smoke test","session_id":"runbook-recovery"}'
```

Expected: HTTP 200 with `episodic_id`, `engram_id`, etc.

---

## 5. Post-incident checklist

Run these within 24 hours of declaring resolution:

- [ ] Update the status page to "resolved".
- [ ] Pin the incident-summary message in the team channel.
- [ ] Save `pg_stat_activity` snapshot from Section 2.4 to `docs/incidents/<date>-postgres.md` along with the resolution timeline.
- [ ] Verify the next scheduled backup ran successfully (Supabase: Project → Backups; Render: Database → Backups).
- [ ] If the outage exceeded the documented **RTO of 4 hours** (see `BACKUP_RESTORE.md`), file an issue tagged `reliability` to revisit the RTO target.
- [ ] If a single query was responsible: open a PR to add an index, rewrite the query, or guard with a per-route timeout below 30s.
- [ ] If pool exhaustion was responsible: open a PR to widen the pool (`db_pool_size`, `db_max_overflow`) only after verifying the underlying transaction-leak was fixed; widening the pool around a leak just delays the next incident.

---

## 6. Escalation path

| Trigger | Action |
|---|---|
| DB unreachable for >15 minutes | Open a Supabase / Render support ticket with the project ID and `pg_stat_activity` snapshot. |
| DB unreachable for >30 minutes | Page the founder. Begin preparing a `BACKUP_RESTORE.md` execution. |
| Confirmed data loss (Section 4.3 mismatch) | Page the founder immediately. **Do not** clear `READ_ONLY=true`. Follow `BACKUP_RESTORE.md`. |
| Schema mismatch on recovery (4.2 fails) | Roll forward via `scripts/run_with_migrations.sh`. If migrations fail, capture the alembic error and escalate before retrying. |
