# BACKEND_HARDENING_PLAN.md

**Goal:** Bring the NexMem backend to *private beta* readiness.
**Scope:** Security, correctness, reliability, and testing only. No new product features, no SDK work, no docs cosmetics, no billing.
**Method:** Small, atomic, code-verified changes. Every change has a test or a reproducible verification step.

This plan is the *backbone* for the work. Risk classifications and code-pointer evidence live in `BACKEND_RISKS.md`.

---

## 0. Operating principles

- **Fail closed.** Insecure defaults must crash the app at startup, not log a warning.
- **Verify from code.** Each fix has a file:line reference and a test, manual repro, or migration verification.
- **No optimism.** "Should work" is not a fix. Either prove it from the test, or mark it as *unverified*.
- **Atomic PRs.** One concern per change. Bisectable.
- **No drift.** Status docs (`PROJECT_STATUS.md`, `PRODUCTION_READINESS_PLAN.md`) must be updated to match reality whenever a fix lands; otherwise the docs continue to lie to investors.

---

## 1. Order of work (dependency order)

The order is not arbitrary. Items earlier in the list unblock later items.

| # | Item | Why first | Risk if skipped |
|---|---|---|---|
| 1 | Remove hardcoded Supabase credentials | The leaked password is a present, ongoing exposure. | DB takeover. |
| 2 | Hard-fail on `DEMO_MODE=true` in production | One env-var flip from total auth bypass. | Silent disable of every auth check. |
| 3 | Hard-fail on weak `SECRET_KEY` in production | Default secret is published; tokens are forgeable. | All JWTs forgeable. |
| 4 | Fix CORS default and require strict origins in production | `allow_credentials=True` with `*` is insecure. | CSRF + cross-origin data theft. |
| 5 | Guard migration `007_standardize_vector_dim` | Currently `DELETE FROM semantic_memory` unconditionally. | Production data loss. |
| 6 | Wire `check_quota` into write routers | Quotas are decorative until called. | Free-tier abuse → unbounded OpenAI bill. |
| 7 | Make demo-mode actually work in tests | Auth router hits DB even in demo, breaks CI. | All "demo-mode" tests today are broken. |
| 8 | Replace CI demo-mode-only tests with Postgres + Redis integration tests | Today's CI is theatre. | Auth/RLS regressions land silently. |
| 9 | Add real RLS / auth / quota / migration tests in CI | Without these, items 1–6 cannot be regression-protected. | Fixes will rot. |
| 10 | Fix `locustfile.py` and stale `test_memory.py` assertions | They falsely claim coverage; harms diligence. | Misleading test "coverage." |
| 11 | Demote false "✅ Complete" claims in status docs | Investor-facing docs must not contradict the code. | Diligence trust loss. |

Items 12+ (in §6) are smaller but still required before private beta.

---

## 2. Critical fixes (must land before private beta)

### C1. Purge hardcoded Supabase credentials

**Code evidence:**
- `alembic/env.py:35-37` — full `postgres.***REDACTED_PROJECT_ID***:***REDACTED_PASSWORD***@aws-1-ap-northeast-1.pooler.supabase.com` URL hardcoded as a fail-safe override.
- `scripts/apply_migrations.py:7` — same password embedded in plain text.
- `render.yaml:14, 33` — Supabase project ref `***REDACTED_PROJECT_ID***` and pooler hostname hardcoded (no password, but identifies the deployment target).

**Fix:**
1. Delete the fail-safe override block from `alembic/env.py`. If `DATABASE_URL` is missing or stale, fail fast with a clear `RuntimeError`. No silent fallback, ever.
2. `scripts/apply_migrations.py` should read `DATABASE_URL` from the environment. If unset, exit non-zero.
3. In `render.yaml`, replace the value of `DATABASE_URL` with `sync: false` so the URL has to be supplied via the Render dashboard. The blueprint must be sharable across environments.
4. Add a pre-commit hint or README note instructing users to rotate the existing Supabase password.

**Verification:**
- `git grep -E "Doesitmatter|***REDACTED_PROJECT_ID***"` returns no hits in source files.
- `python scripts/apply_migrations.py` without `DATABASE_URL` exits with a clear error.
- `alembic upgrade head` with no `DATABASE_URL` raises `RuntimeError` (not asyncpg connection error).
- New unit test `tests/test_alembic_env.py` confirms missing `DATABASE_URL` raises before any network call.

**Out of scope of this PR:**
- Actually rotating the password in the Supabase dashboard (this is an ops task, not a code change). Documented in `BACKEND_RISKS.md` § Rotations Required.

---

### C2. Block `DEMO_MODE=true` in production

**Code evidence:**
- `app/core/deps.py:18-22` — `if settings.demo_mode: return synthetic User(DEMO_USER_ID)`. Auth disabled entirely.
- `app/config.py:115-141` — `validate_production` only logs warnings; was raised, then downgraded ("*Removed the RuntimeError raise to prevent startup hangs.*").

**Fix:**
- In `validate_production`, when `environment == "production"` (case-insensitive):
  - **Hard-fail** if `demo_mode` is truthy.
  - **Hard-fail** if `secret_key` matches the published default or `len < 32`.
  - **Hard-fail** if `allowed_origins == ["*"]` and `len > 0`.
- Call `validate_production()` from `Settings.__init__` *and* from the FastAPI lifespan, so neither path can skip it.
- Document `DEMO_MODE` as **dev-only** in `.env.example` and `README.md`.

**Verification:**
- `tests/test_config_safety.py::test_demo_mode_blocked_in_production` constructs a `Settings(environment="production", demo_mode=True)` and asserts `RuntimeError`.
- Same file: `test_weak_secret_blocked_in_production`, `test_wildcard_cors_blocked_in_production`.

---

### C3. Hard-fail on weak `SECRET_KEY` in production

Bundled with C2 (above). The default secret `"local-dev-secret-change-this-before-production"` is in the repo, so any production deploy that forgets to override it has all JWTs forgeable.

---

### C4. Lock down CORS default

**Code evidence:**
- `app/config.py:81` — `allowed_origins: Union[str, List[str]] = ["*"]`.
- `app/main.py:131-137` — `CORSMiddleware(..., allow_origins=settings.allowed_origins, allow_credentials=True, ...)`.

**Fix:**
- In production, `allow_credentials=True` + `*` must be impossible. Keep the dev default (`["*"]`) but fail-fast in production (covered by C2).
- Additionally, when `allow_credentials=True` and `*` is in the list at runtime, log an error and force `allow_credentials=False` to avoid the worst combination.

**Verification:**
- `tests/test_config_safety.py::test_credentials_disabled_when_wildcard_origin`.

---

### C5. Guard migration 007 against destructive `DELETE`

**Code evidence:**
- `alembic/versions/007_standardize_vector_dim.py:18` — `op.execute("DELETE FROM semantic_memory")` unconditionally.

**Fix:**
1. Detect current vector dimension before deleting. If dim is already 384, skip the DELETE entirely.
2. If dim differs, require `ALLOW_DESTRUCTIVE_MIGRATION=1` env var, otherwise abort with a loud error.
3. Add a doc comment at the top of the file warning future authors.
4. Migration `007_revised` should also `RAISE NOTICE` the row count it intends to drop, so operators know what is being destroyed.

**Verification:**
- New migration test `tests/test_migration_007.py` (run with a real Postgres in CI):
  - Apply migrations through 006.
  - Insert one row with a 384-dim vector.
  - Apply 007 *without* `ALLOW_DESTRUCTIVE_MIGRATION` → migration succeeds, row preserved.
  - Apply on a separate fixture with 1536-dim → migration fails without flag, succeeds with flag.

---

### C6. Wire `check_quota` into write routers

**Code evidence:**
- `app/core/rate_limit_redis.py:38-100` — `check_quota` defined, never called.
- `grep -r 'check_quota' app/` → only the definition file.

**Fix:**
- Create a small dependency in `app/core/quota.py` that calls `check_quota` for the authenticated user.
- Apply the dependency to:
  - `POST /api/v1/agents/{user_id}/episodes` (`episodic.create_episode`)
  - `POST /api/v1/agents/{user_id}/semantics` (`semantic.create_semantic`)
  - `POST /api/v1/memory/episode/write` (`memory.write_episode`)
  - `POST /api/v1/rag/chat` (`rag.rag_chat`)
- The current `check_quota` has bugs:
  - It opens a *new* sync `redis` client per call (the file-level `RedisStorage` already exists). Reuse a single async Redis client.
  - It silently swallows errors. Fail closed when Redis is configured (`REDIS_URL` set) but unreachable; fail open only when no Redis is configured.
- Default tier (per `User.tier`) is "free" with `free_monthly_writes=1000` (matches `config.py`).

**Verification:**
- New `tests/test_quota.py` against an in-memory fakeredis or live Redis service. Cases:
  - Under-quota write succeeds.
  - At-quota write succeeds, increments to limit.
  - Over-quota write returns 429 with structured payload.
  - Enterprise tier bypasses quota.

---

### C7. Make demo-mode tests actually run

**Code evidence:**
- `tests/conftest.py` registers a user via `POST /api/v1/auth/register`; that route does not branch on `settings.demo_mode` and tries to query the real DB.
- Verified live: under `DEMO_MODE=true` and a fake `DATABASE_URL`, `pytest tests/` produces *8 failures + 5 errors* due to asyncpg connect timeouts. The "zero-dependency" claim in `PROJECT_STATUS.md` is false.

**Fix (smallest possible):**
- Add a demo-mode short-circuit to `auth.register`, `auth.login`, `auth.create_api_key`, `auth.list_api_keys`, `auth.delete_api_key`, `auth.refresh_token`, and `auth.get_current_user_info`. The demo flow can return synthetic-user payloads so existing demo-mode tests pass.

  *Or* (preferred long-term):

- Drop demo-mode from CI entirely and run integration tests against a real Postgres+Redis (see C8/C9).

This plan does **both** — short-circuit the bare minimum for the existing tests to be green, then layer real integration tests on top.

**Verification:**
- `pytest tests/ -k 'not slow'` exits 0 with `DEMO_MODE=true` and *no* `DATABASE_URL`.

---

### C8. Real integration tests in CI (Postgres + Redis)

**Code evidence:**
- `.github/workflows/ci.yml:30-40` — only runs demo-mode tests; no `services:` block.
- `tests/test_auth.py:5-7`, `tests/test_memory.py:6-8`, `tests/test_memory_context.py`, `tests/test_engram_processor.py` — all gated behind `RUN_DB_TESTS=1` / `RUN_ML_TESTS=1`. CI never sets either.

**Fix:**
- Add a new CI job `integration-tests`:
  - Postgres 16 + pgvector service container.
  - Redis 7 service container.
  - Apply Alembic migrations to head.
  - Run `pytest tests/` with `DEMO_MODE=false`, `RUN_DB_TESTS=1`, `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test`, `REDIS_URL=redis://localhost:6379/0`.
- Keep the demo-mode unit job for fast feedback, but the merge gate is integration.
- Cache spaCy model + sentence-transformers model so cold-start does not blow the CI minute budget. (`RUN_ML_TESTS=1` only on a nightly job — this is a deferred follow-up.)

**Verification:**
- CI green on a PR with no changes (proves the new job actually runs).
- CI red on a PR that breaks RLS (a deliberate regression in a follow-up branch).

---

### C9. Cover every critical security boundary with tests

Tests required (each in CI's `integration-tests` job):

- `test_auth_real_db.py` — register/login/api-key/refresh against real Postgres.
- `test_rls_isolation.py` — user A cannot read user B's `episodic_memory`, `semantic_memory`, `engrams`, `knowledge_nodes`, `knowledge_edges`, `procedural_memory`. Verifies RLS via a SQL probe with a wrong session GUC.
- `test_quota_enforcement.py` — see C6.
- `test_migrations.py` — `alembic upgrade head` then `alembic downgrade base` then `alembic upgrade head` is idempotent.
- `test_episode_write_partial_failure.py` — verify the unified write path either commits all rows or rolls back. (See R-H1 in `BACKEND_RISKS.md`.)

---

### C10. Fix the broken Locust file and stale assertions

**Code evidence:**
- `tests/locustfile.py:54-63` — POSTs to `/api/v1/episodic/`, the actual route is `/api/v1/agents/{user_id}/episodes`.
- `tests/test_memory.py:21-26` — asserts `data["service"] == "Decentralized AI Memory Layer"`; root returns `"NexMem - Decentralized AI Memory Layer"`.

**Fix:**
- Rewrite `locustfile.py` to target the real routes. Add an `on_start` that captures `user_id` from `/auth/me`.
- Update the service-name assertion in `test_memory.py`.

---

### C11. Reconcile status docs with reality

**Code evidence:**
- `PROJECT_STATUS.md:33-49` — every feature marked ✅ Complete, including "Rate Limiting & Quotas" and "Multi-Tenancy."
- `PRODUCTION_READINESS_PLAN.md` — every checkbox checked.
- Both docs contradict the code (no quota enforcement, partial RLS, broken tests, etc.).

**Fix:**
- Re-mark each item as ✅ / 🟡 / ❌ according to code. Add a "Honest status" section with the items still open.
- Add `BACKEND_RISKS.md` and `BACKEND_HARDENING_PLAN.md` as cross-references at the top.

---

## 3. High-priority follow-ups (post-critical, pre-private-beta)

These are not blockers for landing the critical PRs, but **must** ship before any external private-beta customers connect.

| ID | Item | Code pointer |
|---|---|---|
| H1 | Wrap `memory.write_episode` in a single transaction or explicit savepoint. Currently each step has its own `try/except`, partial writes are accepted. | `app/routers/memory.py:write_episode` |
| H2 | Fix `current_user.app_id` AttributeError in `rag.py`. The `User` model has no `app_id` field; the line will crash on any production rag/chat call once `logger.info` is reached. | `app/routers/rag.py:153, 232` |
| H3 | Replace `apps.register_app(app_name: str, description: str = "")` query-string params with a Pydantic body. | `app/routers/apps.py:24-31` |
| H4 | API-key auth path does not set RLS GUC in the `user_context_middleware`. The middleware only handles `Bearer`. Add an explicit `set_rls_context` for API key auth in `deps.get_current_user` (already partially present) and confirm RLS via integration test. | `app/main.py:200-216`, `app/core/deps.py:103` |
| H5 | Sentry sample rates are 1.0; lower to 0.1 traces / 0.0 profiles in production. | `app/main.py:39-44` |
| H6 | `/metrics` token compare uses `==`. Switch to `secrets.compare_digest`. | `app/main.py:267-272` |
| H7 | `engrams` table lacks `app_id` column. Engram-level multi-app scoping is leaky. | `app/models/engram.py` |
| H8 | RLS policies are missing on `users`, `api_keys`, `token_usage`. Add policies and a migration. | (no migration exists yet) |
| H9 | Add scheduled cleanup of expired episodic memories. `cleanup_expired_episodic_memory` exists but is invoked only manually. | `app/main.py:284-291` |
| H10 | Add a migration lock so multiple replicas cannot race `alembic upgrade head` at startup. | `Dockerfile`, `render.yaml` |

---

## 4. Medium-priority follow-ups (pre-public-launch)

| ID | Item | Code pointer |
|---|---|---|
| M1 | Drop the `_generate_demo_reply` LLM-failure fallback. Surface a real `503` so customers see outages. | `app/routers/rag.py:25-71` |
| M2 | Replace per-process NetworkX graph with a query-on-demand model or a Redis-backed shared graph. | `app/services/engram_processor.py` |
| M3 | Pre-download spaCy / sentence-transformers / cross-encoder models in the Docker image. | `Dockerfile` |
| M4 | Add JWT refresh-token revocation via a denylist or kid-rotation. | `app/core/security.py` |
| M5 | Add scope enforcement to API keys. Currently every key has `scopes='read,write'` and nothing checks it. | `app/routers/auth.py:create_api_key` |
| M6 | Reconcile the manual SQL files (`apply_migrations_supabase.sql` etc.) with Alembic. They drift today. | repo root |
| M7 | Add `pip-audit` (Python) and `npm audit` (TS SDK / landing) to CI; add Dependabot. | `.github/workflows/` |
| M8 | Replace `bandit --severity-level high` gate with `medium`. | `tests/run_security_audit.py` |

---

## 5. Test coverage targets after this work lands

CI must include, per merge:

- **Unit tests** (demo-mode where applicable): config validation, password hashing, embedder fallback, security helpers, schema validation.
- **Integration tests** (Postgres + Redis):
  - Auth: register, login, refresh, API-key issue/revoke, login lockout after failed attempts.
  - RLS isolation: cross-user reads return empty; cross-user writes blocked.
  - Quotas: free/starter/pro/enterprise tiers; correct 429 payload.
  - Migrations: `upgrade head` and full down/up cycle on empty DB.
  - Critical write/recall: episode/write → context → rag/chat round trip.
- **Smoke load test**: 30s `locustfile` run against a single worker; failure threshold 1 % errors.

---

## 6. Out of scope (intentionally deferred)

The user instructions explicitly forbid touching these in this pass:

- Billing, Stripe / Paddle integration.
- SDK changes (`nexmem-js`, `nexmem-py`).
- MCP server changes (`nexmem-mcp`).
- Marketing site / Streamlit dashboard.
- Refactors that move logic between modules without changing behavior.
- Performance tuning of the cross-encoder / spaCy pipeline.
- Any cosmetic or doc-only change to the landing site.

These are tracked in the audit (`REPO_STATE_AUDIT.md`) but are not blockers for private beta correctness.

---

## 7. Done definition (for this hardening pass)

- [ ] No source file in the repo contains a Supabase password or pooler URL.
- [ ] `Settings()` raises a clear `RuntimeError` on:
  - production + demo mode
  - production + default / weak `SECRET_KEY`
  - production + wildcard CORS
- [ ] Migration 007 is non-destructive on already-correct schema; destructive path requires explicit env flag.
- [ ] `check_quota` is called on every memory-write route and has its own integration test.
- [ ] CI runs against real Postgres + Redis. Auth, RLS, and quota tests are green.
- [ ] `tests/locustfile.py` targets real routes.
- [ ] `tests/test_memory.py` service-name assertion passes.
- [ ] `PROJECT_STATUS.md` and `PRODUCTION_READINESS_PLAN.md` honestly reflect the code (no false ✅).
- [ ] All work merged to a single feature branch with bisectable commits and a single PR for review.
