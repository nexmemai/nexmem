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



---

## 8. Outcome (filled in at end of Phase 2)

This section is the post-mortem. It is filled in only after all
ten steps have shipped, and is the primary thing reviewers should
read when evaluating "is this ready for first user traffic?".

### 8.1 Summary of changes

| Step | Title | Notable artefacts |
|------|-------|-------------------|
| P2-S0 | Planning + risk register | `BACKEND_HARDENING_PHASE2.md`, `BACKEND_RISKS.md` |
| P2-S1 | Secret hygiene | `scripts/scan_secrets.py`, `scripts/run_with_migrations.sh`, fixed `alembic/env.py`, scrubbed three operator scripts, `render.yaml` -> `sync: false` |
| P2-S2 | Auth + sessions | `app/models/auth.py::RefreshToken`, migration 012, `app/core/demo_auth.py`, `app/core/security.py::decode_token`, `/auth/logout`, `/auth/logout-all`, refresh rotation + replay rejection, validate_production now raises |
| P2-S3 | RLS + app scope | `docs/APP_SCOPING.md`, migration 013 (RLS on api_keys / refresh_tokens / token_usage / users), removed `current_user.app_id` AttributeError path |
| P2-S4 | Transactional writes | `/memory/episode/write` precomputes NLP outside the DB transaction and rolls back atomically on any failure |
| P2-S5 | Bounded concurrency | `app/core/concurrency.py` with embedder/nlp/reranker pools, all heavy callers refactored, Sentry conservative sampling + scrubbing |
| P2-S6 | Migration + deployment safety | `CONTRIBUTING.md`, Redis-aware `/health/ready`, advisory-lock wrapper |
| P2-S7 | Observability | New `app/middleware/logging.py` with `request_id` / `user_id` / `app_id` / `latency_ms`, PII redaction tested |
| P2-S8 | Quota enforcement | `app/core/quotas.py` with write + read caps, wired into all targeted routes |
| P2-S9 | Test suite quality | pytest-cov in CI, unit-isolation sentinel test, marker discipline |
| P2-S10 | Docs truth pass | rewrote `PROJECT_STATUS.md` and `PRODUCTION_READINESS_PLAN.md` |

### 8.2 Test counts

| | Before Phase 2 | After Phase 2 |
|---|---|---|
| Unit tests passing | 0 (the test baseline could not load — `Settings` rejected the test env) | 75 |
| LLM-dependent tests skipping cleanly | 33 | 33 |
| Slow tests deselected by default | 0 | 5 |
| Integration tests | 0 | 1 (rollback semantics, real Postgres) |
| CI jobs | 3 | 5 (`secret-scan`, `lint-and-test`, `integration-tests`, `security-audit`, `docker-build`) |

### 8.3 BACKEND_RISKS.md — remaining items

The risk register has been kept current throughout Phase 2. After
this PR, the unresolved items are:

* **R-101** — RLS coverage is comprehensive on user-scoped tables;
  any new user-scoped table must add a policy in the same migration.
* **R-102** — Refresh tokens are revocable. Access tokens are
  short-lived (4h) but not blocklisted. Acceptable for beta.
* **R-107** — NetworkX graph is per-process; web service pinned to
  `--workers 1`.
* **R-201** — Operator must rewrite git history to purge Phase 1's
  leaked password from older commits. CI scanner blocks new leaks.
* **R-203** — App is request-scoped, not a first-class model.
* **R-204** — No live load test yet.
* **R-205** — Migration 007 is destructive and already run; do not
  re-run.

### 8.4 Operator actions still required

See `PRODUCTION_READINESS_PLAN.md §Operator action still required`.
The five items are: history rewrite, Render-side DATABASE_URL set,
REDIS_URL verification, run migration 013, rotate SECRET_KEY.

### 8.5 Go / no-go recommendation

**Go for first private-beta traffic, conditional on the five
operator actions above being completed.**

The backend has:
* No hardcoded secrets in the working tree, with CI guarding the
  state.
* Production startup that refuses to run insecure config.
* Real, tested session revocation.
* App-scoping consistency with no AttributeError path.
* Atomic multi-step writes with no orphan-row class of bug.
* Bounded concurrency on every CPU-heavy async path.
* Migration race eliminated by Postgres advisory lock.
* Structured JSON logs with request id, user id, app id, and PII
  redaction tested.
* Both write and read quotas wired and tested.
* CI gating that catches regressions for every item above.

The remaining risks are documented (`BACKEND_RISKS.md`) and the
operator's pre-launch runbook is concrete
(`docs/INCIDENT_RUNBOOK.md`, `PRODUCTION_READINESS_PLAN.md`).

### 8.6 Suggested PR title

`Phase 2 backend hardening: secrets, sessions, RLS, transactional
writes, async safety, quota enforcement, structured logs, docs truth`
