# Runbook: Redis outage

> **Scope.** What to do when the Redis instance backing rate limiting, brute-force lockout, monthly quotas, the consolidation Celery broker, and the access-token blocklist becomes unreachable or starts returning errors.
>
> **Owner.** Backend on-call. Escalate per Section 6 if Redis does not recover within 30 minutes.
>
> **Related runbooks.** `POSTGRES_OUTAGE.md` (independent dependency), `OPENAI_OUTAGE.md` (independent dependency).

---

## 1. Symptoms

What end-users / operators see:

- API returns **HTTP 503 `{"status":"degraded"}`** from `GET /health/ready`. The `redis` field is `error: ...`.
- Quota-protected write endpoints (`/memory/episode/write`, `/episodic`, `/semantic`, `/procedural`, `/graph`, `/rag/chat`) return **HTTP 503 `Quota service unavailable`** with no `Retry-After`. This is the documented fail-closed behaviour from `app/core/quotas.py::_check_and_increment`.
- Login-rate-limit, register-rate-limit, and per-route slowapi caps stop being enforced cluster-wide and silently degrade to per-process in-memory state. **Per-IP / per-user buckets reset on every replica restart.**
- Brute-force lockout (`app/core/brute_force.py`) silently degrades to a thread-local `dict` on the affected replica. With `--workers 1` this is the same single bucket; with multiple replicas, an attacker can rotate across replicas.
- Celery: `consolidate_user_memory_task` retries with `ConnectionError` against the broker URL. Tasks pile up nowhere because the broker is the queue.
- Access-token blocklist (P3-A5) cannot be checked. JWT decode succeeds without the blocklist guard, so a previously-revoked access token will be accepted again until its `exp` is reached. Refresh tokens are unaffected (DB-backed).
- DLQ inspection (`scripts/dlq_admin.py`) and `consolidation_dlq` Redis list are inaccessible.

`GET /health/live` continues to return 200.

---

## 2. Documented behaviour: fail-closed vs fail-open per subsystem

This is the single most important table to read **before** taking action — recovery decisions depend on which subsystem is failing safely vs unsafely.

| Subsystem | Source file | Behaviour when Redis is configured but unreachable | Risk |
|---|---|---|---|
| **Quotas** (read + write) | `app/core/quotas.py` | **Fail-closed.** `_check_and_increment` raises `HTTPException(503, "Quota service unavailable")` on any `redis.incr` exception. | None for the platform — protects against unmetered usage. **Costs availability.** |
| **Brute-force lockout** (per-email + per-IP) | `app/core/brute_force.py` | **Fail-open to in-memory.** `_get_redis()` returns `None` on connection error; `_mem_*` thread-local fallback takes over. | Cluster-wide lockout is lost. With multi-replica deploys, an attacker can rotate IPs across replicas. **Mitigated locally** by `--workers 1` in `render.yaml`. |
| **slowapi rate limits** (per-route, per-user) | `app/core/rate_limit.py` (`storage_uri = redis_url or memory://`) | **Fail-open.** slowapi's own client raises and the wrapper falls back to per-process counters. Per-IP / per-user buckets are no longer global. | Same as above — distributed clients can outrun the per-replica cap. |
| **Celery broker** (consolidation, DLQ) | `app/celery_app.py` | **Hard fail.** `consolidate_user_memory_task.delay()` raises and the HTTP write path that triggered it returns 500. The HTTP-side write itself succeeded; only the consolidation enqueue is lost. | Consolidation backlog accumulates. After Redis recovers, the next scheduled consolidation tick picks up missed users. |
| **Access-token blocklist** (P3-A5) | `app/core/token_blocklist.py` | **Fail-open.** A revoked access token is accepted until its `exp`. Refresh tokens (DB-backed) are still revocable. | Window of exposure for revoked access tokens equals the access-token TTL (default 4 hours, `settings.access_token_expire_hours`). |
| **Account-lockout escalation** (P3-A7) | `app/core/brute_force.py::_record_account_escalation` | **Local-only.** The escalation state is purely in-memory; Redis is not involved. | Unchanged by a Redis outage. |
| **Health probes** | `app/routers/health.py::_probe_redis` | Reports `error: ...` so `/health/ready` returns 503. | None — surfaces the outage. |

**Operator implication.** A Redis outage is, in the current architecture, a **mixed-mode degradation**:

- The platform stays *available for writes* (quota dependency raises 503 for **users**, not for **all routes**), but unmetered routes pass through.
- Distributed-attack surfaces become per-replica. With single-replica `--workers 1`, this is acceptable for short outages but unsafe for sustained ones.

---

## 3. Immediate actions (first 5 minutes)

### 3.1 Confirm

```bash
curl -fsS https://nexmem-api.onrender.com/health/ready | jq .
```

The `redis` field tells you the failure mode (timeout, connection refused, auth error, …).

If `/health/ready` itself returns 503 only because of Redis (every other check `ok`), the application surface is partly working — see Section 2.

### 3.2 Decide whether to flip read-only

Use this decision matrix:

| Scenario | Action |
|---|---|
| Redis is degraded for **<5 minutes** AND only one replica is running | Do nothing operationally. Quotas fail-closed for the duration; everything else is per-replica. |
| Redis is degraded for **5–30 minutes** | Set `READ_ONLY=true`. Even though writes still work, fail-open subsystems (rate limit, brute-force, blocklist) drift further from their global state the longer the outage persists. |
| Redis is degraded for **>30 minutes** | Keep `READ_ONLY=true` and follow Section 6 escalation. |
| Multi-replica deploy (`--workers >1` or multiple replicas) | Set `READ_ONLY=true` immediately — fail-open subsystems are now genuinely unsafe (per-replica lockout). |

To set read-only: Render dashboard → Web Service → Environment → set `READ_ONLY=true` → Manual Deploy. Reads, health, and session revocation continue.

### 3.3 Stop the consolidation worker (optional)

If `app/tasks.py::consolidate_user_memory_task` is logging a flood of `ConnectionError` to Sentry, stop the Celery worker until Redis recovers:

```
Render → Worker service "celery-worker" → Suspend
```

Do **not** stop Celery beat — it just produces no-op ticks.

### 3.4 Snapshot

```bash
# From a workstation that can reach the Redis instance:
redis-cli -u "$REDIS_URL" --tls ping       # Confirms whether the issue is reachability or the server itself.
redis-cli -u "$REDIS_URL" --tls info server | head
redis-cli -u "$REDIS_URL" --tls info clients
redis-cli -u "$REDIS_URL" --tls memory stats | head -30
```

---

## 4. Diagnosis tree

### 4.1 Is the Redis process alive?

Render dashboard → Redis service → Status. If the upstream is in a known incident, wait — there is no application-side fix.

### 4.2 Is Redis up but blocked?

```bash
redis-cli -u "$REDIS_URL" --tls --latency
redis-cli -u "$REDIS_URL" --tls slowlog get 20
```

If the slowlog shows multi-second `KEYS *` or `SMEMBERS` commands from us, that is **our bug**. The application never issues `KEYS` on the hot path; if it appears, someone is debugging from a `redis-cli` session attached to the production instance — kick them off.

### 4.3 Is Redis out of memory?

```bash
redis-cli -u "$REDIS_URL" --tls info memory | grep -E '(used_memory|maxmemory|mem_fragmentation)'
```

If `used_memory` ≈ `maxmemory`, evictions are happening and rate-limit / quota counters can disappear mid-window. The eviction policy must be `allkeys-lru` (suitable for our cache-y workload). Confirm:

```bash
redis-cli -u "$REDIS_URL" --tls config get maxmemory-policy
```

If it's anything else, set:

```bash
redis-cli -u "$REDIS_URL" --tls config set maxmemory-policy allkeys-lru
```

### 4.4 Is the DLQ list consuming all the memory?

```bash
redis-cli -u "$REDIS_URL" --tls llen nexmem:dlq:consolidation
```

The DLQ is capped at 1000 entries by `settings.dlq_max_entries`, but only when the producer side honours the cap. If LLEN is large, inspect with `scripts/dlq_admin.py` and trim:

```bash
# Inspect.
python scripts/dlq_admin.py list --limit 20

# Trim to last 500 entries.
redis-cli -u "$REDIS_URL" --tls ltrim nexmem:dlq:consolidation -500 -1
```

### 4.5 Is the rate-limit storage poisoned?

slowapi stores keys under prefixes like `LIMITER/user:<sub>/...`. A bad client can flood many short-TTL keys; under `allkeys-lru` they will evict on their own. If `dbsize` is in the millions and growing, manually scan and delete prefixes:

```bash
redis-cli -u "$REDIS_URL" --tls --scan --pattern 'LIMITER/*' | xargs redis-cli -u "$REDIS_URL" --tls del
```

> Caveat: deleting LIMITER keys resets every per-user / per-IP rate-limit bucket. Do this only as a last resort.

---

## 5. Recovery procedure

After Redis is reachable again:

### 5.1 Verify connectivity

```bash
redis-cli -u "$REDIS_URL" --tls ping     # Expect: PONG
```

### 5.2 Confirm rate-limit Redis reconnects

slowapi auto-reconnects on the next request. Confirm:

```bash
# Trigger a rate-limit-counted request.
curl -fsS -X POST https://nexmem-api.onrender.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@nope.example","password":"wrongpassword"}'
# Inspect the X-RateLimit-* response headers — they should be present.

# Inspect Redis directly:
redis-cli -u "$REDIS_URL" --tls --scan --pattern 'LIMITER/*' | head
```

If the LIMITER prefix is empty after a few successful requests, slowapi never reconnected. Restart the web service.

### 5.3 Confirm Celery picks up queued tasks

Beat will tick within `consolidation_interval_minutes` (default 30). Force an immediate tick by triggering manually:

```bash
# Render dashboard → celery-worker service → Shell tab → run:
python -c "from app.services.scheduler import trigger_consolidation; trigger_consolidation()"
```

Then watch the worker logs for `consolidate_user_memory_task` succeeding. The DLQ length should drop (or stay flat) — if it grows after recovery, the task itself is failing for a non-Redis reason and `OPENAI_OUTAGE.md` may apply.

### 5.4 Confirm brute-force lockout reconnected

The next failed login fires `_get_redis()` again. There is no operator-visible signal beyond a successful `INCR login_fail:...` against Redis. Spot-check:

```bash
redis-cli -u "$REDIS_URL" --tls keys 'login_fail:*' | head
```

### 5.5 Re-enable writes

```
READ_ONLY=false
```

Trigger a deploy. Verify `/health/ready` is `200` and `checks.redis` is `"ok"`.

---

## 6. Post-incident checklist

- [ ] Update status page to "resolved".
- [ ] Save the Redis `INFO`, slowlog, and `MEMORY STATS` snapshots from Section 3.4 to `docs/incidents/<date>-redis.md`.
- [ ] If the access-token blocklist was bypassed during the outage, **manually revoke any token JTI** that was on the blocklist before the outage, by adding a refresh-token revocation row in DB and forcing the user to re-login.
- [ ] If quotas fail-closed locked out paying users, post an apology + reset their quota:
      ```bash
      redis-cli -u "$REDIS_URL" --tls del "quota:write:<user_id>:$(date -u +%Y-%m)"
      ```
- [ ] If the DLQ was trimmed in 4.4, file an issue with the dropped task IDs.
- [ ] Open a follow-up issue if multi-replica fail-open behaviour was observed in 2.x. Long-term the rate-limit and blocklist reads should fail-closed when Redis is configured (matches the quota subsystem's policy).

---

## 7. Escalation path

| Trigger | Action |
|---|---|
| Redis unreachable for >15 minutes | Open a Render / Upstash support ticket with project ID and `INFO server` output. |
| Redis unreachable for >30 minutes on a multi-replica deploy | Page the founder. Confirm `READ_ONLY=true` is set. |
| Confirmed access-token replay during the outage | Page the founder immediately. Begin token-rotation procedure for affected users. |
| Memory eviction policy is wrong (4.3) | Fix the policy, then file a follow-up to add a runtime config check at startup so the wrong policy refuses to boot. |
