# Backend Risk Register

**Last updated:** 2026-05-22 (Phase 2 hardening branch).

This document is the source of truth for backend risk. It is updated
on every hardening pass. Do not delete entries; mark them
`resolved (PR #...)` or `accepted` with rationale.

Severity scale:
- **P0** — blocks first real-user traffic.
- **P1** — should be fixed before first real-user traffic.
- **P2** — known limitation, accepted for private beta.
- **P3** — nice-to-have.

---

## P0 — blockers for first real-user traffic

### R-001 Supabase password leaked in `alembic/env.py` history
- **State on entry to Phase 2:** present in HEAD as a fallback override.
- **Status:** **resolved in working tree** by Phase 2. The fallback is
  removed; `alembic/env.py` now requires `DATABASE_URL` from env.
- **Operator action still required:** rewrite git history to remove
  the password from past commits (see `docs/INCIDENT_RUNBOOK.md`).
- **CI guard added:** `scripts/scan_secrets.py` blocks the password
  pattern from re-entering HEAD.

### R-002 `render.yaml` embedded Supabase pooler URL
- **State on entry:** `DATABASE_URL` was a hardcoded string with the
  Supabase project ref. Rotating the pooler endpoint required a code
  change.
- **Status:** **resolved.** `DATABASE_URL` is now `sync: false` so the
  operator must set it in the Render dashboard.

### R-003 `validate_production` did not enforce
- **State on entry:** the function logged warnings then `pass`-ed.
- **Status:** **resolved.** `validate_production` now raises
  `RuntimeError` in non-demo mode if `SECRET_KEY` is weak/default,
  if `DATABASE_URL` is unset, or if `ALLOWED_ORIGINS` is `*`.

### R-004 `current_user.app_id` `AttributeError`
- **State on entry:** `app/routers/rag.py` referenced
  `current_user.app_id` in two `logger.info` calls. The `User` model
  has no `app_id` attribute, so this raises on any RAG request.
- **Status:** **resolved.** The code now reads `app_id` from the
  request body (which already carries it) and stops referencing the
  non-existent attribute. App scoping is documented as
  *request-scoped* in `docs/APP_SCOPING.md`.

### R-005 Quota enforcement was never wired
- **State on entry:** `check_quota` existed in
  `app/core/rate_limit_redis.py` but no router imported or called it.
- **Status:** **resolved.** New
  `app/core/quotas.py::enforce_write_quota` dependency is wired into
  episodic, semantic, procedural, graph, and `memory.episode/write`
  write routes. Unit tests cover allow/deny and Redis-down behaviour.

### R-006 Engine is created at import time with hard validator
- **State on entry:** `Settings()` raised if `DATABASE_URL` was empty,
  even in demo mode. This made the entire test suite refuse to load
  unless a fake URL was injected before import.
- **Status:** **resolved.** The validator now permits empty
  `DATABASE_URL` when `DEMO_MODE=true` and falls back to a sqlite
  in-memory URL (engine is never used in demo mode).
  `validate_production` carries the strict check in non-demo mode.

### R-007 Migration race in multi-replica deploy
- **State on entry:** every web replica started with `alembic upgrade
  head` in `render.yaml` `startCommand`. Replicas race on first deploy.
- **Status:** **resolved.** The `startCommand` now uses
  `scripts/run_with_migrations.sh` which acquires a Postgres
  `pg_try_advisory_lock` and runs migrations only on the holder.

### R-008 RLS context leak in `get_current_user`
- **State on entry:** `get_current_user` called
  `set_current_user_id(str(user.id))` but never reset the contextvar.
  Worker reuse across requests would carry stale identity.
- **Status:** **resolved.** The HTTP middleware now owns the
  contextvar lifecycle, and `get_current_user` only sets it on the
  request, not the global contextvar.

---

## P1 — should ship before first real-user traffic

### R-101 RLS is only enforced on memory tables
- `api_keys`, `users`, `token_usage`, and any future webhook tables
  do not have RLS policies. Service-role queries are unconstrained.
- **Status:** **partial.** Phase 2 keeps API key reads scoped via the
  ORM but does not add RLS to those tables. Tracked here so it does
  not get lost.

### R-102 No real session revocation
- Refresh tokens are issued but not stored. They cannot be revoked
  before expiry. Phase 2 documents this and adds a logout endpoint
  that, in the absence of token storage, invalidates by best-effort
  (no-op locally; instructs the SDK to drop the token).
- **Mitigation:** access tokens are short-lived (4 hours).
- **Future work:** add a `refresh_tokens` table with revocation.

### R-103 Sentry config is unsafe defaults
- **State on entry:** Sentry was initialized with `traces_sample_rate=1.0`
  and `profiles_sample_rate=1.0`, no PII scrubbing.
- **Status:** **resolved.** Phase 2 sets traces to `0.1`, profiles to
  `0`, and adds a `before_send` hook that strips Authorization
  headers, cookies, and known PII fields.

### R-104 Health endpoint does not check Redis
- `/health/ready` only checks the DB. Redis is required for rate
  limits and brute-force protection.
- **Status:** **resolved.** `/health/ready` now checks Redis if
  `REDIS_URL` is set, and reports the result individually.

### R-105 Multi-step writes are not transactional
- `/memory/episode/write` writes to four tables with intermediate
  awaits. A mid-chain failure can leave orphan rows.
- **Status:** **resolved (production path).** The production code now
  wraps the four-step write in a single `async with db.begin():`
  block and rolls back on any failure. The demo path is in-memory
  and unchanged.

### R-106 Blocking NLP / embedding work in async routes
- `embedder.embed` is async and uses a thread executor with a
  semaphore. `engram_processor.process_async` and `llm_service.generate*`
  also use threads. These were already mostly safe but lacked a
  global cap.
- **Status:** **partial.** The existing per-event-loop semaphore is
  retained. A startup log entry now records that models are loaded
  lazily on first use, with a warning that production should
  pre-warm them.

### R-107 NetworkX graph state is per-process
- `engram_processor` keeps an in-memory NetworkX graph that is not
  shared across workers. The startup rebuild reads from DB but each
  process holds its own copy.
- **Status:** **documented.** The web service is configured to run
  with a single worker (`--workers 1`) until a shared store is
  introduced. This is reflected in `render.yaml` and
  `docs/DEPLOYMENT.md`. Alternative architectures are tracked as P2.

### R-108 Read quotas are not enforced
- Phase 2 wires write quotas. Read quotas (recall, RAG) are not
  enforced.
- **Status:** **partial.** Phase 2 adds an `enforce_read_quota`
  dependency that is wired into `/memory/context` and `/rag/chat`,
  but with a much higher default ceiling (`free_monthly_reads = 10000`).
  Tests cover allow/deny.

### R-109 Token expiry test missing
- **Status:** **resolved.** Phase 2 adds a unit test that an expired
  JWT is rejected by `get_current_user`.

### R-110 Logging middleware was unstructured
- **Status:** **resolved.** The middleware now emits a single JSON
  log line per request via structlog with `request_id`, `user_id`,
  `app_id`, `method`, `path`, `status`, `latency_ms`. A test
  asserts that no Authorization header value appears in a log line.

---

## P2 — accepted for private beta

### R-201 Full git-history rewrite required
- The leaked Supabase password remains in older commits on `main`
  and on the previous Phase 1 PR branch. The CI scanner blocks new
  occurrences but cannot rewrite history.
- **Operator action:** follow `docs/INCIDENT_RUNBOOK.md`.

### R-202 Demo path and production path duplicate logic
- `/memory/episode/write` and several other routes have separate
  demo-mode and production-mode branches. This is a long-running
  refactor that is out of scope for Phase 2.

### R-203 No formal "App" model
- App scoping is request-scoped via the `app_id` query parameter or
  request body field. There is no `apps` table. `app_registry` uses
  the `api_keys.scopes` column to encode `app:<uuid>` strings. This
  is documented and consistent across routers, but it is not the
  long-term shape.

### R-204 No load testing under realistic conditions
- A `locustfile.py` exists but has not been run against the
  production database. This is a scaling-time risk, not a launch
  blocker, given the expected initial traffic.

### R-205 Migration 007 is destructive
- `007_standardize_vector_dim.py` runs `DELETE FROM semantic_memory`
  unconditionally. It has already been run on the live database and
  cannot be re-run safely. Phase 2 fences it with a guard comment;
  proper protection requires a post-migration audit script.

### R-206 No backup / restore documentation
- Postgres backups are Supabase's responsibility, but there is no
  documented restore drill.

---

## P3 — nice to have

- Migrate from `python-jose` to `pyjwt` (jose is in maintenance mode).
- Add `bandit` to CI (already in dev deps; not yet wired).
- Add `mypy` strict mode for `app/core/`.
- Replace `passlib[bcrypt]==1.7.4` with a maintained alternative.
