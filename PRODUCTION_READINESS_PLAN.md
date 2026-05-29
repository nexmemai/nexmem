# Production Readiness Plan

**Honest assessment.** Last verified by direct code inspection on branch
`backend/hardening-private-beta`.

> Status legend: ✅ done · 🟡 partial · ❌ not started.
>
> See also: [`PROJECT_STATUS.md`](./PROJECT_STATUS.md),
> [`BACKEND_HARDENING_PLAN.md`](./BACKEND_HARDENING_PLAN.md),
> [`BACKEND_RISKS.md`](./BACKEND_RISKS.md), and [`REPO_STATE_AUDIT.md`](./REPO_STATE_AUDIT.md).

The previous version of this document marked every checkbox ✅. The code did
not match. This rewrite reflects what the code actually does.

---

## Phase 1 — Security, Authentication, Multi-Tenancy

| Task | Status | Code-verified evidence |
| :--- | :---: | :--- |
| 1.1 API Key Management | ✅ | `mem_*` keys, SHA-256 hashed, constant-time verify; `auth.create_api_key` / `list_api_keys` / `delete_api_key`; integration test `test_auth_real_db.py::test_api_key_lifecycle`. |
| 1.2 Rate Limiting and Quotas | 🟡 | slowapi global 60/min IP limit (`app/core/rate_limit.py`). **NEW:** per-user monthly quota (`app/core/quota.py`) wired into all four write routes; integration test `test_quota_real_redis.py`. Tier-based; not yet billable. |
| 1.3 Secrets Management | 🟡 | DATABASE_URL et al. read from env / Render dashboard. No Vault / Doppler / Secrets Manager integration. The previously-leaked Supabase password has been removed from the repo (C1) but **the password itself must still be rotated and git history scrubbed** (ops task). |
| 1.4 (NEW) Production fail-fast | ✅ | `Settings.validate_production` raises `RuntimeError` on `DEMO_MODE=true`, weak `SECRET_KEY`, wildcard `ALLOWED_ORIGINS`, or placeholder `DATABASE_URL` host. Lifespan calls it, so the server cannot bind a port in unsafe production. Tests: `test_config_safety.py`, `test_app_startup_safety.py`. |
| 1.5 (NEW) RLS coverage | 🟡 | RLS enabled+forced on episodic / semantic / procedural / knowledge_nodes / knowledge_edges / engrams (m008). **Missing on `users`, `api_keys`, `token_usage`** — tracked as R-H7. |

---

## Phase 2 — Database Scalability and Performance

| Task | Status | Evidence |
| :--- | :---: | :--- |
| 2.1 PgBouncer / Connection Pooling | 🟡 | PgBouncer is in `docker-compose.yml` for local. **Not** in `render.yaml` (production); the path connects directly to the Supabase pooler. |
| 2.2 pgvector HNSW Indexing | ✅ | HNSW on `semantic_memory.vector` (m=16, ef_construction=200) and `engrams.dense_embedding` (m=16, ef_construction=64). |
| 2.3 Database Migrations | 🟡 | 12 Alembic revisions; head is `011_fk_cascade_content_limits`. Migration 007 was destructive on every upgrade — **fixed in C5**: now non-destructive on already-correct schema, gated behind `ALLOW_DESTRUCTIVE_MIGRATION=1` otherwise. Manual SQL files (`apply_migrations_supabase.sql` etc.) still drift from Alembic — R-M4. `alembic upgrade head` runs in the container start command with no migration lock — R-H10. |

---

## Phase 3 — Background Processing and Reliability

| Task | Status | Evidence |
| :--- | :---: | :--- |
| 3.1 Message Queue | ✅ | Celery 5.4 with Redis broker; beat schedules consolidation every 30 min. |
| 3.2 LLM Resiliency | ✅ | tenacity exponential-backoff retries on OpenAI calls (`app/services/llm.py`). |
| 3.3 Dead Letter Queues | ❌ | `consolidate_user_memory_task` logs `"DLQ [DEAD LETTER QUEUE]"` after `max_retries`. There is **no actual dead-letter queue**; messages are dropped. Tracked as R-C/H. |

---

## Phase 4 — Observability and Monitoring

| Task | Status | Evidence |
| :--- | :---: | :--- |
| 4.1 Structured Logging | ✅ | structlog JSON, request-ID middleware. |
| 4.2 APM (Prometheus) | 🟡 | `prometheus-fastapi-instrumentator` wired; `/metrics` endpoint Bearer-protected. No Prometheus scrape verified yet. |
| 4.3 Error Tracking (Sentry) | 🟡 | Init wired when `SENTRY_DSN` is set. Sample rates are 1.0 — cost bomb at any traffic (R-H9). |
| 4.4 Cost / Token Tracking | 🟡 | `token_usage` table exists; rows logged via structlog from `rag.py`. **Latent bug:** `current_user.app_id` is logged but the User model has no `app_id` field — will `AttributeError` at runtime (R-H2). Not yet aggregated for billing. |

---

## Phase 5 — CI / CD and Deployment Automation

| Task | Status | Evidence |
| :--- | :---: | :--- |
| 5.1 Containerization | ✅ | Multi-stage Dockerfile, non-root user, reproducible build. |
| 5.2 CI/CD Pipeline (GitHub Actions) | ✅ | Three real jobs: `unit-tests` (lint + mypy + 56 demo-mode tests), `integration-tests` (real Postgres + Redis services, runs auth/RLS/quota/migration tests), `security-audit` (bandit on HIGH). `docker-build` runs on main. |
| 5.3 Backend Deployment | 🟡 | `render.yaml` blueprint exists. **All four services on the free tier**, which is incompatible with the SLA implied by the marketing site. `DATABASE_URL` is now `sync: false` (must be set via Render dashboard). |
| 5.4 Frontend Deployment | ✅ | Vercel via `.github/workflows/deploy-frontend.yml`. |

---

## Phase 6 — Testing and Quality Assurance

| Task | Status | Evidence |
| :--- | :---: | :--- |
| 6.1 Unit & Integration Tests | 🟡 | **Was broken before this PR** — auth router was not demo-aware so even demo-mode tests hit Postgres. **Now real:** unit tests run cleanly without external deps; integration tests run against real Postgres + Redis service containers in CI. ML-heavy tests (`RUN_ML_TESTS=1`) are deferred to a planned nightly job. |
| 6.2 Load Testing | 🟡 | `tests/locustfile.py` was POSTing to non-existent routes — **fixed in C10**. Now points at real routes; route-guard test in CI prevents regression. The locust run itself is not in CI. |
| 6.3 Security Audit (SAST) | 🟡 | `bandit` runs in CI. Currently fails on HIGH only; medium-severity findings accumulate silently. Add `pip-audit` and `npm audit` next. |

---

## What this PR (`backend/hardening-private-beta`) added

- C1 — credential purge + repo-wide regression test.
- C2/C3/C4 — production fail-fast for `DEMO_MODE`, weak `SECRET_KEY`, wildcard CORS, and placeholder DB URLs.
- C5 — non-destructive migration 007.
- C6 — wired per-user monthly quota dependency on every write route.
- C7 — auth router branches on demo mode; "zero-dependency tests" claim is finally true.
- C8/C9 — real Postgres + Redis integration job in CI.
- C10/C12 — locustfile + stale-assertion fixes.

---

## What this PR did **not** ship (deferred to follow-up PRs)

The user-instructed scope of this hardening pass excluded billing, SDKs,
MCP, and cosmetic refactors. The following items remain open and are
documented in [`BACKEND_HARDENING_PLAN.md`](./BACKEND_HARDENING_PLAN.md) §3
and [`BACKEND_RISKS.md`](./BACKEND_RISKS.md):

- Billing pipeline (Stripe / Paddle) and customer-facing onboarding UI.
- RLS on `users`, `api_keys`, `token_usage`.
- `app_id` column + FK on engrams.
- FK from memory tables to `users` with `ON DELETE CASCADE`.
- API-key auth must set RLS GUC in middleware.
- `apps.register_app` Pydantic body.
- Refresh-token revocation.
- Sentry sample rates ≤ 0.1.
- Migration runs out of the start command.
- Drop the `_generate_demo_reply` LLM-failure fallback.
- pgaudit / append-only audit log.

---

## Definition of "Production Ready"

Private beta — what we are aiming for now:

- ✅ No exposed secrets in source.
- ✅ Production cannot start in unsafe configuration.
- ✅ Migrations cannot silently destroy data.
- ✅ Quotas enforced on all write paths.
- ✅ Real integration tests in CI (auth, RLS, quotas, migrations).
- ⏳ Honest status documentation matches the code (this document).

Public launch — additional gates beyond private beta:

- Billing + invoicing.
- RLS on auth tables.
- Refresh-token revocation, MFA, password reset.
- Audit log.
- Multi-region deploy (or explicit single-region documented in DPA).
- SOC-2 controls in flight.
