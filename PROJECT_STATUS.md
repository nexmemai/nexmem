# Nexmem Project Status

**Last reviewed:** 2026-05-22 (end of Phase 3 — auth & session hardening).
**Stage:** pre-private-beta. Has not yet served real user traffic.

This document is the source of truth for what is actually shipped vs
what is planned. It is rewritten end-of-phase rather than maintained
incrementally because the previous version was overclaiming. If a
feature is not listed below as "shipped", assume it is not in the
product.

---

## 1. What the backend is

A persistent memory layer for LLM agents, exposed as a small FastAPI
service. Stores four "memory types" — episodic, semantic, procedural,
and a graph (associative) — plus distilled "engrams" derived from
the raw episodic stream.

Every record is scoped by `user_id`. Records may also carry an
optional `app_id` for the same user to keep agents distinct. App
scoping is request-scoped (body or query parameter), not part of the
authenticated identity. See `docs/APP_SCOPING.md` for the rule and
`tests/test_app_scoping.py` for the contract.

---

## 2. Tech stack

* **Backend:** FastAPI on Python 3.11.
* **Database:** PostgreSQL 16+ with `pgvector`, `pg_trgm`, and Row
  Level Security. Phase 2 extends RLS to `users`, `api_keys`,
  `refresh_tokens`, and `token_usage` (was previously memory-only).
* **Cache / queue:** Redis. Used for rate limiting, brute-force
  lockout, monthly write/read quotas, and Celery broker.
* **Background jobs:** Celery worker + beat. Used for memory
  consolidation; previous in-process scheduler is retained as a
  fallback path but is not the production target.
* **AI services:**
  * Embeddings — `all-MiniLM-L6-v2` (384-d) via
    `sentence-transformers`, lazy-loaded on first request.
  * Rerank — `cross-encoder/ms-marco-MiniLM-L-6-v2`, lazy-loaded.
  * NLP — spaCy `en_core_web_sm` for entity / action / object
    extraction.
  * LLM — OpenAI `gpt-4o` for RAG, `gpt-4o-mini` for consolidation.
* **Observability:** structlog JSON logging with `request_id`,
  `user_id`, `app_id`, `route`, `status`, `latency_ms`.
  Sentry initialized when `SENTRY_DSN` is set, with conservative
  sample rates (`traces=0.1`, `profiles=0`) and a `before_send` hook
  that strips Authorization, Cookie, X-Api-Key headers and
  password / refresh_token / api_key body fields.
* **Deployment target:** Render. `render.yaml` provisions web,
  Celery worker, Celery beat, and Redis. Migrations run via
  `scripts/run_with_migrations.sh` which acquires a Postgres
  advisory lock so multi-replica deploys cannot race.

---

## 3. What is shipped

### 3.1 Auth & sessions
* Email/password registration and login with bcrypt-hashed
  passwords (passlib + bcrypt 4.0.1).
* JWT access tokens (HS256, whitelisted; alg=none cannot fall
  through). Default 4-hour expiry; expired tokens are rejected.
* Refresh tokens stored hashed in the `refresh_tokens` table with
  `revoked_at` column. `/auth/refresh` rotates the token and rejects
  replays. `/auth/logout` revokes the current session.
  `/auth/logout-all` revokes every session.
* API keys — `mem_<urlsafe-base64>` form, stored as SHA-256 hashes,
  looked up with `secrets.compare_digest`. `DELETE /auth/api-keys/{id}`
  hard-deletes so a leaked key stops working immediately.
* Brute-force lockout on login: 5 failures within 10 minutes locks
  the email and IP for 15 minutes. Uses Redis when available,
  in-memory otherwise (single-worker only — see R-107).
* **Phase 3 (post-Phase 2):**
  * Email verification on registration. `/auth/verify-email/confirm`
    consumes a single-use 24-hour token and stamps
    `users.email_verified_at`. Login refuses to mint tokens for an
    unverified email user when `EMAIL_VERIFICATION_REQUIRED=true`
    (operator opt-in, defaults to `false`). Resend endpoint returns
    a generic 202 to avoid account enumeration. (P3-A1.)
  * Password reset flow. `/auth/password-reset/request` issues a
    single-use 30-minute token; `/auth/password-reset/confirm`
    rotates the password and revokes every active refresh token.
    Always returns 202 on request to avoid leaking which addresses
    are registered. (P3-A2.)
  * Authenticated `/auth/change-password` requires the current
    password and revokes all refresh tokens on success. (P3-A3.)
  * `GET /auth/sessions` lists active refresh tokens with their
    user_agent, ip, issued/expires timestamps. `DELETE
    /auth/sessions/{id}` revokes a single session. (P3-A4.)
  * Per-IP rate limit on `/auth/register` (default `5/hour`)
    enforced via slowapi. Demo mode is exempted so tests run
    without throttling. (P3-A8.)

### 3.2 Memory writes
Five tables: `episodic_memory`, `semantic_memory`,
`procedural_memory`, `knowledge_nodes`, `knowledge_edges`, plus
`engrams`. Writes carry optional `app_id`. Every user-scoped table
has RLS enabled and forced; `users_login_lookup` SELECT policy
permits unauthenticated lookups by email for the login flow.

`POST /api/v1/memory/episode/write` (the unified write endpoint)
runs in a single transaction. NLP / embedding / engram precompute
happens before the transaction opens, so a slow embedding call does
not hold a DB transaction open. A mid-chain failure rolls back the
entire write — no orphan rows. (R-105.)

### 3.3 Memory reads
* Unified context assembly at `POST /api/v1/memory/context` returns
  ranked semantic hits, recent episodes with decay scores, the user's
  preferences, and the engram summary, capped to a token budget.
* `POST /api/v1/rag/chat` performs hybrid retrieval (vector +
  full-text + graph) and feeds the result to the LLM with a
  redacted prompt.

### 3.4 Quotas
Phase 2 introduces `app/core/quotas.py`:
* `enforce_write_quota` is wired into every write route. Per-tier
  caps (`free=1k`, `starter=10k`, `pro=100k`, enterprise infinite)
  are read from settings.
* `enforce_read_quota` is wired into `/memory/context` and
  `/rag/chat` with much more generous caps.
* TTL is set on the first INC of the month and not reset on
  subsequent INCs; quotas reset on the first day of the next month
  (UTC) by Redis key expiry.
* Production fails closed (HTTP 503) when REDIS_URL is set but
  unreachable.

### 3.5 Async safety
`app/core/concurrency.py` provides three named semaphore pools
(`embedder=4`, `nlp=4`, `reranker=2`). Every CPU-heavy synchronous
call from an async route handler goes through `run_bounded(pool,
fn, …)` so the executor cannot spawn unbounded threads under burst
traffic.

### 3.6 Migrations
* Alembic chain runs through `alembic/env.py` which acquires
  `pg_try_advisory_lock(728_419_362_001)` before applying any
  migration. The first replica wins; the rest skip the upgrade
  and exit normally.
* `scripts/run_with_migrations.sh` is the production entry point
  (set as the Render `startCommand`).
* Migration authoring rules in `CONTRIBUTING.md`.

### 3.7 Observability
* Single structured JSON log line per request with `request_id`,
  `user_id`, `app_id`, `method`, `path`, `status`, `latency_ms`,
  `client_ip`, `user_agent`. Authorization/Cookie/X-Api-Key
  headers and credential-shaped body fields are never logged.
  Tested in `tests/test_logging_redaction.py`.
* `X-Request-ID` echoed back in every response. Client values are
  honoured if 8–64 chars and alnum/-/_; junk is replaced with a
  fresh synthesized id.
* `/health/live` is fast and unconditional. `/health/ready` checks
  Postgres (with latency), Redis (when configured), and the
  embedding service. Slow DB probes (>1000 ms) are flagged.

### 3.8 CI
GitHub Actions runs three jobs per PR:
1. `secret-scan` — `python scripts/scan_secrets.py --ci`. Blocks
   the merge on any pattern hit.
2. `lint-and-test` — flake8 syntax check + pytest unit suite with
   `pytest-cov`. Coverage XML uploaded as artefact.
3. `integration-tests` — runs `pgvector/pgvector:pg16` and
   `redis:7` service containers and executes
   `pytest -m integration` with `RUN_DB_TESTS=1`.
4. `security-audit` — `tests/run_security_audit.py` (bandit).

---

## 4. Known limitations for private beta

These are real and are tracked in `BACKEND_RISKS.md`. They are
acceptable for first private beta but every operator should know
them.

* **R-101 RLS coverage is narrow.** Phase 2 extends RLS to
  `api_keys`, `refresh_tokens`, `token_usage`, and `users` itself.
  Future user-scoped tables must add their own RLS policy in the
  same migration.
* **R-102 Refresh tokens are revocable but access tokens are not.**
  An access token compromised mid-session remains valid until its
  4-hour expiry. We do not yet have an access-token blocklist.
* **R-107 NetworkX graph is per-process.** The web service is
  pinned to `--workers 1` in `render.yaml`. Bumping the worker
  count without making the graph store shared would give clients
  inconsistent results.
* **R-201 Git history rewrite still pending.** Phase 1's leaked
  Supabase password is gone from the working tree (and CI will
  block its return), but it remains in the older commit chain on
  `main` and on the Phase 1 PR branch. Operator action documented
  in `docs/INCIDENT_RUNBOOK.md`.
* **R-203 No first-class `apps` model.** App scoping is an
  optional column on each user-scoped table, validated against the
  current user. Adequate for private beta. A real `apps` table is
  future work.
* **R-205 Migration 007 is destructive.** `DELETE FROM
  semantic_memory` ran once on the live database during the
  initial 384-d migration; it cannot be re-run without losing
  data. Documented and fenced.

---

## 5. What is NOT shipped

Listed honestly so reviewers do not assume features are present.

* No billing / Stripe / subscription productization.
* No SDK published to PyPI / npm. `nexmem-py` and `nexmem-js` exist
  as in-repo packages but are not published.
* No MCP-server hardening beyond a smoke-tested skeleton.
* No new connectors, webhooks, or third-party integrations.
* No cosmetic UI work in Phase 2.

---

## 6. Test counts (end of Phase 2)

* **Unit tests:** 75 passing locally. CI runs the same set with
  `pytest-cov` and uploads coverage.
* **LLM-dependent tests:** 33 skip cleanly when no OpenAI key is
  configured.
* **Slow tests:** 5 deselected by default (full NLP pipeline,
  HuggingFace model downloads). Run on demand with `pytest -m slow`.
* **Integration tests:** 1 (`tests/test_transactional_writes_integration.py`)
  exercises rollback against a real Postgres + Redis. Runs in
  the integration job.

Phase 1 reported "56 unit tests passing"; that figure could not be
reproduced because the `Settings` validator was rejecting the
default test environment. Phase 2 fixed the test baseline as
P2-S0 / P2-S2 work; the 75 number is the post-fix count.
