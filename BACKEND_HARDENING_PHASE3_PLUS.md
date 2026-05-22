# Backend Hardening — Phase 3 and beyond

**Last reviewed:** 2026-05-22 (immediately after Phase 2 PR #3).
**Scope:** every backend area that still needs hardening after Phase 2,
grouped by phase with priorities and acceptance criteria.

This document is the source of truth for "what's left." It is the
companion to:
- [`BACKEND_HARDENING_PHASE2.md`](BACKEND_HARDENING_PHASE2.md) — what shipped in Phase 2.
- [`BACKEND_RISKS.md`](BACKEND_RISKS.md) — the live risk register; entries here reference R-IDs there.
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — what is shipped vs deferred.

Priorities used below:
- **P0** — must ship before scaling beyond a tiny private beta.
- **P1** — should ship before charging customers / before public beta.
- **P2** — required before SOC2 / enterprise.
- **P3** — nice to have.

Each item carries a stable ID like `P3-A2` so future PRs can reference
it. The phase number reflects roughly when the work should land, not a
hard ordering — items can be picked up in any sensible bundle.

---

## Phase 3 — Auth & session hardening (post-beta)

The Phase 2 work made sessions revocable and JWT decoding safe. The
next layer is the *human* parts of auth: password reset, email
verification, 2FA, account recovery.

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P3-A1 | **Email verification on registration** | P1 | Currently anyone can register any email. We send a token via email; user must confirm before login is allowed. Blocks bot signups burning the free quota. Adds a `users.email_verified_at` column + `/auth/verify-email` route. Token signed with `SECRET_KEY`, single-use, 24h expiry. |
| P3-A2 | **Password reset flow** | P1 | `/auth/password-reset/request` and `/auth/password-reset/confirm`. Token is single-use, 30-min expiry, hashed in `password_reset_tokens` table. Successful reset revokes every refresh token (P2-S2 plumbing already exists). |
| P3-A3 | **Password change endpoint** | P1 | `POST /auth/change-password` requires current password + new password. Revokes all refresh tokens on success. Useful even before full reset flow ships. |
| P3-A4 | **Session listing endpoint** | P1 | `GET /auth/sessions` returns active refresh tokens (id, user_agent, ip, last_used_at). `DELETE /auth/sessions/{id}` revokes one. Builds on the `refresh_tokens` table from migration 012. |
| P3-A5 | **Access-token blocklist** | P2 | R-102 in `BACKEND_RISKS.md`. Currently access tokens are valid until their 4h expiry. A short-circuit blocklist (`access_token_blocklist` table or Redis SET with TTL == token expiry) lets us kill an active session immediately. |
| P3-A6 | **2FA / TOTP** | P2 | Optional opt-in. Uses `pyotp`. Adds `users.totp_secret`, `users.totp_enabled`. Login flow gets a second step. Recovery codes hashed in a side table. Required for SOC2 path. |
| P3-A7 | **Account lockout escalation** | P2 | Brute-force per email + per IP works (P2-S2). Add a global counter "this user has had 50 failures across N IPs in the last hour" → temporarily lock the account and notify the user. |
| P3-A8 | **Rate limit on `/auth/register`** | P1 | Currently no per-IP cap. A bot farm could create unlimited free accounts. Add `slowapi` decorator with `5/hour` per IP. |
| P3-A9 | **CAPTCHA / proof-of-work on signup** | P3 | Hand-off to a managed service (hCaptcha, Turnstile) when traffic profile demands it. Skip for early beta. |
| P3-A10 | **API key rotation flow** | P2 | Currently rotation = create new + delete old. Add a single endpoint `POST /auth/api-keys/{id}/rotate` that does it atomically and returns the new raw key. Keep the old key valid for a grace period (24h) so callers can swap. |

**Acceptance criteria:** P1 items (A1–A4, A8) ship before billing.
Each has a unit test that fails before the fix and a route that the
existing structured-logging pipeline already covers.

---

## Phase 4 — Multi-tenant model (apps as first-class)

R-203 in the risk register. Right now apps are encoded in
`api_keys.scopes` as `app:<uuid>` strings. This is fine for private
beta but breaks down once a customer has more than a handful of apps
or wants to do app-level analytics, billing, or RBAC.

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P4-B1 | **Create `apps` table** | P1 | `id (UUID pk)`, `user_id (FK)`, `name`, `description`, `created_at`, `is_active`. RLS enabled. Backfill migration reads `api_keys.scopes` and creates rows. |
| P4-B2 | **Migrate `api_keys` to `app_id (FK)`** | P1 | Add `api_keys.app_id` column nullable; backfill from existing scopes; deprecate `scopes` field. Two-deploy roll: add+backfill in deploy N, drop `scopes` in deploy N+2. |
| P4-B3 | **App registration quota** | P1 | `/apps/register` currently has no rate limit and no per-user cap. A user could spam-create thousands of apps. Add `enforce_write_quota` plus a hard per-user cap (default 50 apps; raise per-tier). |
| P4-B4 | **App-level RLS policies** | P2 | Once `apps` exists, RLS on memory tables can use `(user_id, app_id)` instead of just `user_id`. Guarantees one app cannot read another's data even if a query forgets to filter. |
| P4-B5 | **App-level metrics + quotas** | P2 | Today quotas are per-user. Customers will want per-app caps so a noisy app doesn't blow the whole user budget. Adds `quota:write:<user_id>:<app_id>:<month>` keys. |
| P4-B6 | **App suspension** | P2 | Operator-side flag to pause an app (writes refused, reads still work). Useful during incidents or billing disputes. |
| P4-B7 | **Cross-app data sharing rules** | P3 | Default is full isolation. If we ever want "share semantic memory across my apps," it needs an explicit opt-in column on `apps` and an audit trail. Document the default-deny rule in `docs/APP_SCOPING.md`. |

**Acceptance criteria:** B1–B3 land together in a single migration
chain. Cross-app isolation tests already exist
(`tests/test_app_scoping.py`) — they should pass against the new
schema with no changes.

---

## Phase 5 — Database & migration hardening

Postgres is the single most-load-bearing component. Phase 2 fixed the
race; Phase 5 tunes for stable production behaviour and adds the
guard-rails the schema needs for long-term operation.

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P5-C1 | **Per-connection statement timeout** | P0 | `app/database.py` does not set `statement_timeout` or `idle_in_transaction_session_timeout`. A runaway query can pin a Supabase pooler connection forever. Set to 30s and 60s respectively via `connect_args["server_settings"]`. |
| P5-C2 | **Connection pool sizing** | P1 | Pool is hardcoded at 5/5. Document the math (Render plan × workers × pool size ≤ Supabase pooler max). Make it env-tunable: `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`. |
| P5-C3 | **Episodic memory partitioning** | P2 | `episodic_memory` will grow without bound. Range-partition by `created_at` (monthly) once the table approaches ~10M rows. Drop-old-partitions becomes a simple admin operation. |
| P5-C4 | **Archival policy for old data** | P2 | Move episodic rows older than 90 days to a colder partition or to S3. Document a retention default per tier. Required for GDPR data minimisation. |
| P5-C5 | **Forward+back migration test** | P1 | CI runs `alembic upgrade head` against the integration DB but does not test downgrade. Add a job that runs `upgrade head → downgrade -1 → upgrade head` and asserts no schema drift. |
| P5-C6 | **Migration safety lint** | P1 | Pre-commit hook (or CI step) that scans new migrations for: unconditional `DELETE FROM`, `DROP TABLE` of non-empty tables, column type changes without a temp column, `ALTER` statements that would take ACCESS EXCLUSIVE on tables > 1M rows. Rules from `CONTRIBUTING.md §3.2`. |
| P5-C7 | **Replica lag check in `/health/ready`** | P2 | When we add a Postgres read replica, readiness must include lag-bounded check. Today there is no replica — track here so it's not forgotten. |
| P5-C8 | **Foreign-key audit** | P1 | `engrams` has no FK back to `episodic_memory.id` even though they're produced together. `knowledge_edges → knowledge_nodes` FK exists but the ON DELETE behaviour was not exercised under load — add an integration test. |
| P5-C9 | **CHECK constraints on JSON fields** | P2 | `episodic_memory.metadata`, `procedural_memory.settings`, etc. are `JSONB` with no shape validation at DB level. Add CHECK constraints (`jsonb_typeof = 'object'`) and document the schema in code. |
| P5-C10 | **Backup restore drill** | P0 | R-206 in risk register. Supabase takes daily backups; we have never restored one. Quarterly drill, time-boxed at 1 hour, documented in `docs/INCIDENT_RUNBOOK.md`. |

**Acceptance criteria:** C1, C5, C6, C8, C10 are the must-haves before
public beta. C2 documented; C3/C4 deferred until traffic justifies it.

---

## Phase 6 — Background processing & Celery

Celery is configured but not battle-hardened. Several known gaps from
the Phase 2 audit that were out of scope:

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P6-D1 | **Real DLQ for failed consolidation** | P1 | `consolidate_user_memory_task` logs `critical` on exhausted retries but loses the payload. Send to a dedicated Celery queue `consolidation_dlq` so operators can inspect, fix, and replay. |
| P6-D2 | **Task time / soft time limits** | P0 | `celery_app.conf` has none. A consolidation that hangs on OpenAI will pin a worker forever. Set `task_soft_time_limit=240`, `task_time_limit=300`. |
| P6-D3 | **`worker_max_tasks_per_child`** | P1 | Long-running workers leak memory from spaCy / sentence-transformers. Set to 100 so workers recycle. |
| P6-D4 | **`result_expires` for the result backend** | P1 | Defaults are too long; Redis fills up. Set to 3600s. |
| P6-D5 | **Idempotency on `consolidate_user_memory_task`** | P1 | If the task is enqueued twice for the same `(user_id, days_old)` window, work is duplicated. Use a Redis SETNX lock keyed `consolidation:<user_id>:<window>` with TTL = task_time_limit. |
| P6-D6 | **NLP/LLM moved out of DB transaction (consolidation)** | P1 | `consolidate_episode` calls `summarize_with_llm` inside the implicit DB transaction managed by `db.commit()`. Move precompute outside the open session, then open a transaction only for the writes. Mirror what P2-S4 did for `/memory/episode/write`. |
| P6-D7 | **Circuit breaker around OpenAI** | P1 | `tenacity` retries individual calls; nothing trips out when OpenAI is globally down. After N consecutive failures in a 1-min window, refuse new RAG / consolidation calls for M seconds with a clear 503. |
| P6-D8 | **Backpressure when queue depth high** | P2 | Celery has no built-in backpressure. Add a Redis counter for queue depth; when above 10× workers, return 503 from the trigger endpoint instead of enqueuing. |
| P6-D9 | **`consolidate_all_users` RLS bypass** | P1 | The task iterates every user and calls `consolidate_for_user` per user. Each call must set RLS context for that user before issuing SQL. Phase 2 wired this for the request path; the Celery path needs the same treatment. |
| P6-D10 | **Per-task structured logging** | P2 | Celery tasks today log via stdlib `logging`; events do not flow into the structured pipeline used by HTTP. Add a `before_task_publish`/`task_prerun` signal that injects task_id, user_id into structlog contextvars. |

**Acceptance criteria:** D1, D2, D5, D6, D9 ship before any real
consolidation traffic. The rest can follow.

---

## Phase 7 — Input safety & abuse vectors

Phase 2 added quotas. It did not address input shapes that can OOM the
worker, request body sizes, or the unprotected admin-shaped routes.

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P7-E1 | **Streaming GDPR export** | P0 | `/memory/user/{user_id}/export` loads every row into memory, builds a Python dict, and returns it as JSON. A user with 1M episodes OOMs the worker. Stream as NDJSON or chunked array. |
| P7-E2 | **Single-transaction GDPR delete** | P0 | `delete_all_memories` deletes from each table in sequence with a single `commit()` at the end. If one delete fails, partial deletion is observable. Wrap in `async with db.begin():`. |
| P7-E3 | **Audit log of GDPR actions** | P1 | New table `gdpr_audit_log` records every export and delete with user_id, ip, user_agent, timestamp. Required for SOC2 / GDPR data subject request handling. |
| P7-E4 | **Soft-delete grace period for GDPR delete** | P2 | Today delete is immediate and irreversible. Mark for deletion in `users.deleted_at`, schedule a Celery task to actually delete after 30 days. Lets the user undo before data is gone. |
| P7-E5 | **Request body size cap** | P0 | FastAPI/Starlette accepts arbitrarily large bodies. A 1 GB POST will OOM the worker before any validator runs. Add a middleware that 413s anything over 5 MB. |
| P7-E6 | **JSON depth/size DoS guards** | P1 | A deeply nested JSON object can consume CPU during pydantic validation. Cap nesting at 32 levels and total object count at 10k. Implement as a custom validator on the Settings root. |
| P7-E7 | **Per-route rate limits** | P1 | slowapi default is per-IP only and applies to every route equally. Add explicit decorators on `/auth/register` (5/hour), `/auth/login` (20/min), `/apps/register` (10/hour), `/memory/episode/write` (use existing quota), `/rag/chat` (use existing quota). |
| P7-E8 | **Per-authenticated-user rate limit** | P1 | The current rate limit is per-IP. A single attacker behind a CDN hitting many IPs can still pound a single user. Add a slowapi key function that uses `current_user.id` if authenticated, else IP. |
| P7-E9 | **Tighten error responses** | P1 | Several routes do `raise HTTPException(detail=f"...{exc}")`. Internal exception messages can leak schema. Replace with generic messages; full detail goes to logs and Sentry only. |
| P7-E10 | **Maximum response size cap** | P2 | Listing routes have `limit` capped at 200 / 500. Document the per-route response-size budget so operators can tune ahead of memory pressure. |

**Acceptance criteria:** E1, E2, E5 are P0 because they are real DoS
vectors today. The rest follow.

---

## Phase 8 — Observability deepening

Phase 2 shipped structured logs + Sentry. Phase 8 wires the rest of
the operational picture.

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P8-F1 | **Distributed tracing** | P1 | OpenTelemetry with auto-instrumentation for FastAPI, SQLAlchemy, httpx, redis, celery. Traces export to whatever backend the operator chooses (Tempo, Honeycomb, Datadog). Critical when LLM + Celery + Postgres + Redis chains stretch latency budgets. |
| P8-F2 | **Per-task structured logs (Celery)** | P1 | Same tooling as P6-D10. |
| P8-F3 | **SLOs + error budgets** | P1 | Define SLOs in code-as-config (e.g. p95 latency < 800ms on `/rag/chat`, < 200ms on `/memory/context`, error rate < 0.5%). Phase 8 ships the targets; alerting wires later. |
| P8-F4 | **Alerting rules** | P1 | Sentry alerts on error rate spike, structured-log alerts on quota lockouts, Prometheus alert on Postgres pool saturation. Document who pages whom in `docs/INCIDENT_RUNBOOK.md`. |
| P8-F5 | **Cold-start latency metric** | P2 | Phase 2 added load-time logs. Promote to a Prometheus histogram so we can see "first request after cold deploy" trend over time. |
| P8-F6 | **Embedding queue depth metric** | P2 | The bounded `embedder` pool from `app/core/concurrency.py` doesn't expose its waiting queue. Add a metric so we can see when the cap is binding. |
| P8-F7 | **Celery queue depth + worker liveness in `/health/ready`** | P1 | Today readiness checks DB + Redis + embedder. Add Celery worker count + queue depth so a stuck worker doesn't pass readiness. |
| P8-F8 | **Log retention policy** | P2 | Document where logs go (Render? Datadog?), how long they're kept, and any redaction at the storage tier. Phase 2 wrote redaction at the application; storage-tier policy is operator work. |

**Acceptance criteria:** F1, F3, F4, F7 are P1 because they're the
gap between "we have logs" and "we can run an incident."

---

## Phase 9 — Reliability & disaster recovery

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P9-G1 | **Read-only mode (kill switch)** | P0 | A single env-var-flag `READ_ONLY=true` causes every write route to 503. Lets the operator stop the bleeding during a runaway-cost or data-corruption incident. Wire as a global FastAPI middleware. |
| P9-G2 | **Graceful shutdown** | P1 | uvicorn's default SIGTERM handling drops in-flight requests. Confirm `--graceful-shutdown` semantics; for Celery, ensure `task_acks_late=True` and `worker_prefetch_multiplier=1` so a SIGTERM doesn't drop tasks. |
| P9-G3 | **Postgres outage runbook** | P0 | "What does the operator do when Supabase is down?" Document failure modes (writes 5xx, reads degrade, /health/ready goes 503), expected client behaviour, recovery steps. |
| P9-G4 | **Redis outage runbook** | P0 | Today: brute-force protection falls back to in-memory; quotas fail closed; rate limits fall back to in-memory. Document each subsystem's behaviour explicitly. Run a chaos test (remove REDIS_URL, see what breaks). |
| P9-G5 | **OpenAI outage runbook** | P1 | RAG and consolidation are the only paths that hit OpenAI. With the circuit breaker (P6-D7), behaviour is "503 for a while, then retry". Document. |
| P9-G6 | **Backup restore drill** | P0 | Same as P5-C10. Quarterly. Required before public beta. |
| P9-G7 | **Multi-region story** | P3 | Single-region for now. Document explicitly so reviewers don't assume HA. |

**Acceptance criteria:** G1, G3, G4, G6 before public beta. The rest
when traffic justifies.

---

## Phase 10 — Compliance & audit

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P10-H1 | **`gdpr_audit_log` table** | P1 | Same as P7-E3. Each row: user_id, action (export / delete / consent_change), ip, user_agent, timestamp, request_id. Append-only via DB role. |
| P10-H2 | **`auth_audit_log` table** | P1 | Login successes, login failures, password changes, API key creations, refresh-token revocations, 2FA enrolments. Operators need this for incident response and SOC2. |
| P10-H3 | **Data retention policy** | P2 | Documented retention per data class: episodic 365 days default, semantic indefinite, refresh tokens until revoked + 90 days. Code-side cron deletes anything past retention. |
| P10-H4 | **DPA / SOC2 collateral** | P2 | Operator work; not in code. Track here so it's not lost. |
| P10-H5 | **`SECURITY.md`** | P1 | Standard file describing how to report a vulnerability, expected response time, scope. GitHub picks it up automatically. |
| P10-H6 | **Dependency vulnerability scanning** | P1 | `pip-audit` (or `safety`) in the CI pipeline alongside bandit. Block merges with HIGH/CRITICAL CVEs in dependencies. |
| P10-H7 | **CodeQL or equivalent SAST** | P2 | bandit catches Python anti-patterns; CodeQL catches data-flow issues. Wire as a separate CI job that doesn't block merges initially. |

**Acceptance criteria:** H1, H2, H5, H6 before public beta or first
paying customer.

---

## Phase 11 — Operator tooling

These don't affect end-user safety but make the difference between
"operator can fix it in 5 minutes" and "operator pages the on-call
engineer at 3am."

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P11-I1 | **Operator CLI** | P1 | A `nexmem-admin` command (in-repo) that authenticates with an admin API key and exposes: rotate-secret-key, list-users, force-revoke-key, force-logout-user, mark-app-suspended, replay-dlq-task. Avoids hand-crafted SQL during incidents. |
| P11-I2 | **Auditable support impersonation** | P2 | Sometimes support needs to "view as user" to debug. Issue a special impersonation JWT that carries `actual_user_id != effective_user_id`, logs every action with both ids, expires fast. Required for SOC2. |
| P11-I3 | **`force-logout-user` endpoint** | P1 | `/auth/logout-all` revokes the *current* user's sessions. We need an admin variant that takes a user_id, with audit log. Useful when a user reports a stolen device. |
| P11-I4 | **Usage analytics for operators** | P2 | A small admin-only endpoint that returns: total active users, writes/reads per day, top-N apps by write volume, current Celery queue depth. Avoids needing a separate BI tool for the first 1k customers. |
| P11-I5 | **Replay tooling for the DLQ** | P1 | Once P6-D1 ships a real DLQ, add a CLI command to inspect, fix, and replay failed tasks. |

---

## Phase 12 — Ecosystem (out of pure-backend scope)

These are not strictly backend-hardening but every one of them is a
backend dependency that a customer will trip over. Listed for tracking;
each likely deserves its own phase document.

| ID | Item | Pri | Notes |
|----|------|-----|-------|
| P12-J1 | **`nexmem-py` SDK tests + publish** | P1 | The SDK exists in-repo with no test suite and no PyPI release. Customers cannot install it. Hardening: add pytest + tox matrix, publish under our org. |
| P12-J2 | **`nexmem-js` SDK tests + publish** | P1 | Same as above for npm. |
| P12-J3 | **MCP server input validation** | P2 | `nexmem-mcp/server.py` accepts `text`, `query`, `metadata`, `key`, `value` as MCP tool params with no length / type validation beyond what the API itself enforces. Add explicit validation so a malformed MCP client fails fast with a clear error. |
| P12-J4 | **MCP server timeout + retry policy** | P2 | The HTTP client uses `tenacity` retries already, but no global request budget. A bad MCP client could keep a worker thread busy for minutes. Set `httpx.Timeout(connect=5, read=30, write=30)` and a hard 60s overall budget. |
| P12-J5 | **Frontend (Streamlit + Next.js) auth integration** | P2 | The Streamlit dashboard at `frontend/app.py` has no auth flow tied to the backend; the Next.js landing has no JWT integration. Required before any UI is exposed to non-operators. |
| P12-J6 | **API versioning policy** | P1 | All routes are `/api/v1/...`. Document the deprecation policy: minor breaks get a 6-month overlap window, major breaks bump to `/api/v2/`. |

---

## Carry-over from `BACKEND_RISKS.md`

These risks were tracked at end of Phase 2. They are not phase-numbered
above because they are operator actions or are absorbed into one of
the phases. They remain in the live register until closed.

| Risk ID | Status | Where it lives now |
|---------|--------|--------------------|
| R-101 | partial | New user-scoped tables get RLS in P4-B4 (app-level RLS). |
| R-102 | open | P3-A5 (access-token blocklist). |
| R-107 | open | Stays single-worker until shared graph store ships; tracked under no specific phase. |
| R-201 | operator-only | `docs/INCIDENT_RUNBOOK.md`. Not a code change. |
| R-202 | accepted | Demo path / production path duplication. Not currently in any phase plan; revisit when refactor cost is justified. |
| R-203 | open | Phase 4 in full (B1–B3 mandatory). |
| R-204 | open | Load testing — tracked under P9 reliability work but no specific ID; will be set up alongside G3/G4 chaos tests. |
| R-205 | accepted | Migration 007 destructiveness; documented. |
| R-206 | open | P5-C10 / P9-G6 (the same drill counts for both). |

---

## Suggested ordering

If we treat these as four future PRs:

**PR-A — "before public beta" (P0/P1):**
P5-C1 (statement timeout), P5-C5 (downgrade test), P5-C8 (FK audit),
P6-D1/D2/D5/D6/D9 (Celery hardening), P7-E1/E2/E5/E7 (input safety),
P9-G1/G3/G4 (read-only mode + outage runbooks), P10-H5/H6
(SECURITY.md + pip-audit), P11-I1/I3 (operator CLI), P12-J6 (API
versioning).

**PR-B — "before billing" (P3 + multi-tenant + audit):**
P3-A1/A2/A3/A4/A8 (email verify, password reset, change, sessions,
register rate limit), P4-B1/B2/B3 (apps as first-class), P10-H1/H2
(audit logs), P12-J1/J2 (SDK tests + publish).

**PR-C — "before SOC2" (P2 items):**
P3-A5/A6/A7 (access blocklist, 2FA, lockout escalation), P4-B4/B5/B6
(app-level RLS, quotas, suspension), P5-C3/C4/C9 (partitioning,
retention, JSON checks), P6-D3/D4/D7/D8 (Celery polish), P7-E3/E4/E6
(GDPR audit, soft-delete, JSON DoS), P8-F1/F3/F4/F7 (tracing, SLOs,
alerting, ready-deepening), P9-G2/G5/G6 (graceful shutdown, OpenAI
runbook, backup drill), P10-H3/H4/H7 (retention, DPA, CodeQL),
P11-I2/I4/I5 (impersonation, analytics, DLQ replay), P12-J3/J4/J5
(MCP polish, frontend auth).

**PR-D — "nice to have" (P3):**
P3-A9/A10 (CAPTCHA, key rotation), P4-B7 (cross-app sharing rules),
P9-G7 (multi-region story).

Each PR follows the `P<phase>-S<step>` commit convention from Phase 2.

---

## Notes for whoever picks this up

1. Cross-link every PR back to the relevant `P<phase>-<id>` in this
   document so reviewers can audit "is this a real, prioritised
   item?"
2. Update `BACKEND_RISKS.md` whenever an item closes — change its row
   from `open` to `resolved (PR #...)` rather than deleting it.
3. The `CONTRIBUTING.md §3` migration-authoring rules and §6 logging
   redaction rules apply to every item in this plan; they were the
   long-tail outputs of Phase 2 and should not be re-derived.
4. Before starting any phase, re-read `BACKEND_HARDENING_PHASE2.md §1`
   so the reconciliation discipline that caught Phase 1's overclaims
   carries forward.
