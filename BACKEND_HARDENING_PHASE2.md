# Backend Hardening — Phase 2

**Date:** 2026-05-22
**Branch:** `chore/p2-backend-hardening`
**Mode:** safety + correctness sprint (no new product features)
**Driver:** prepare backend for first real-user traffic.

---

## 1. Background

Phase 1 (PR #1) addressed an immediate credential incident and laid the
groundwork for production safety. The operator has since rotated the
Supabase database password and any related service-role keys.

Before Phase 2 work began, the working tree was audited and several
Phase 1 claims did **not** match reality. Phase 2 reconciles those gaps
in addition to the assigned hardening work.

### Phase 1 claims vs reality (verified 2026-05-22)

| Phase 1 claim | Verified state on `main` |
|--------------|-------------------------|
| Credentials removed from HEAD | **Partial.** `alembic/env.py` still contained a hardcoded Supabase password (`postgres.***REDACTED_PROJECT_ID***:Doesitmatter…`). `render.yaml` still embedded the Supabase project ref in `DATABASE_URL`. Phase 2 removes these. |
| `validate_production` hard-fails unsafe startup | **Not true.** The function logs warnings only and ends with `pass`. Phase 2 makes it raise on insecure config in production. |
| Migration 007 guarded | **Not guarded.** `007_standardize_vector_dim.py` performs an unconditional `DELETE FROM semantic_memory`. Phase 2 documents this and fences against accidental re-execution. |
| Quota enforcement wired into write routes | **Not wired.** `check_quota` is defined in `app/core/rate_limit_redis.py` but is never imported or called. Phase 2 wires a new `enforce_write_quota` dependency and adds tests. |
| Demo-mode auth short-circuits | **Confirmed.** `get_current_user` returns the demo user when `DEMO_MODE=true`. |
| Integration test infrastructure added | **Not present.** `tests/` is flat, no `RUN_DB_TESTS` markers, no Postgres/Redis service in CI. Phase 2 adds markers, conftest guards, and an integration job. |
| Status docs reconciled | **Overclaiming.** `PROJECT_STATUS.md` and `PRODUCTION_READINESS_PLAN.md` mark every box as complete. Phase 2 rewrites these to reflect reality. |

This document is the source of truth for what was actually done in
Phase 2. It is referenced from `BACKEND_RISKS.md`.

---

## 2. Objectives

1. Eliminate every secret in HEAD and add a CI tripwire for future leaks.
2. Make production startup refuse to run under unsafe configuration.
3. Make session invalidation real and verifiable.
4. Eliminate the `current_user.app_id` `AttributeError` path and make
   app-scoping rules consistent across every router.
5. Make multi-step write paths transactional or verifiably idempotent.
6. Move blocking NLP / embedding / cross-encoder work off the async
   request thread, with bounded concurrency.
7. Eliminate the multi-replica migration race.
8. Ensure every request produces a structured log line with
   `request_id`, `user_id`, `app_id`, route, status, latency.
9. Enforce both write and read quotas, with documented edge-case
   behaviour.
10. Make CI a meaningful proof of backend correctness, not a comfort
    signal.
11. Reconcile docs with reality. No overclaiming.

---

## 3. Risk items by priority

### P0 — must ship before first real user
- **Hardcoded Supabase URL with password in `alembic/env.py`** —
  full credential leak in HEAD even after operator rotation.
- **`render.yaml` embeds Supabase project URL** — cannot rotate the
  pooler host without a code change. Forces use of `sync: false`.
- **`validate_production` does not enforce** anything.
- **`current_user.app_id` AttributeError** in `rag.py` token-usage logging.
- **No quota enforcement is wired** even though the function exists.
- **Engine is created at import time** with `settings.database_url`,
  which currently makes import fail in demo mode unless `DATABASE_URL`
  is set. This blocks the entire test suite from loading.
- **Migration race**: `render.yaml` runs `alembic upgrade head` from the
  web service start command. Multi-replica deploys race.
- **Get_current_user leaks RLS context** — calls
  `set_current_user_id(...)` without resetting on dependency exit.

### P1 — should ship before first real user
- RLS only covers memory tables. `api_keys`, `users`, `token_usage`
  are unprotected.
- No session/refresh-token revocation; refresh tokens are not stored.
- Logging middleware does not emit structured JSON; no `user_id`,
  `app_id`, or `route` fields.
- Sentry is initialized but with no PII scrubbing and a 100% trace
  sample rate (cost risk).
- `health/ready` does not check Redis even though Redis is required.
- Multi-step writes (`/memory/episode/write`) commit nothing inside a
  single transaction, so a mid-chain failure can leave orphan rows.
- Per-process NetworkX graph state is silently incoherent across
  multiple workers.
- Read quota is not enforced anywhere.

### P2 — document and defer
- Full git-history rewrite of leaked secrets (operator action).
- Move from short-expiry access tokens to a real session blocklist.
- Implement Application/App as a first-class model.
- Refactor demo_db / production write paths to share a single core.
- Migrate `apscheduler` references out of code (Celery is already used).

---

## 4. Out of scope (explicit)

- Billing productization, plan upgrades, Stripe integration.
- SDK improvements (`nexmem-py`, `nexmem-js`, `nexmem-mcp`).
- New connectors or product features.
- Cosmetic refactors.
- Re-architecting demo mode and production write paths.
- Full git-history rewrite (operator-only step, runbook only).

---

## 5. Success criteria

Phase 2 is complete when, at minimum:

- [ ] No hardcoded secrets exist in the current working tree.
- [ ] CI fails on any newly committed secret pattern.
- [ ] Production startup raises in `validate_production` if config is
  insecure.
- [ ] `current_user.app_id` is no longer referenced; app scoping is
  consistent across episodic, semantic, procedural, graph, rag, gdpr,
  and memory routers.
- [ ] `enforce_write_quota` is wired into all write routes and proven
  by a unit test.
- [ ] A failed multi-step write leaves no orphan rows (proven by test).
- [ ] Heavy NLP / embedder calls run via `run_in_executor` with a
  bounded semaphore.
- [ ] Migration startup is gated by a Postgres advisory lock.
- [ ] Every request produces a structured JSON log line with
  `request_id`, `user_id`, `app_id`, route, status, latency.
- [ ] CI runs a meaningful baseline of unit tests; integration suite is
  scaffolded with explicit `integration` markers and skips cleanly when
  `RUN_DB_TESTS=1` is not set.
- [ ] `BACKEND_RISKS.md` accurately enumerates remaining risks.
- [ ] Docs no longer claim features that are not actually present.

---

## 6. Commit pattern

Phase 2 work uses small, bisectable commits prefixed by step:

- `P2-S0` — baseline + planning
- `P2-S1` — secret hygiene
- `P2-S2` — auth and session safety
- `P2-S3` — RLS + app scope
- `P2-S4` — transactional writes
- `P2-S5` — async request path
- `P2-S6` — migration + deployment
- `P2-S7` — observability
- `P2-S8` — quota + billing correctness
- `P2-S9` — test suite quality
- `P2-S10` — docs truth

---

## 7. Operator actions still required after this PR

These are not in scope for the PR itself but are necessary before
first user traffic:

1. Rewrite git history to purge the rotated Supabase password from
   commits before this PR. Use `git filter-repo` or BFG. Steps in
   `docs/INCIDENT_RUNBOOK.md`.
2. Force-push the cleaned history; notify any collaborators to
   re-clone.
3. Manually set `DATABASE_URL` in the Render dashboard for both the
   web service and Celery worker (now `sync: false`).
4. Confirm that the Render Redis instance is provisioned and
   reachable, and that `REDIS_URL` is wired into the web service.
5. Run migrations exactly once via the documented release flow before
   bringing up replicas.
