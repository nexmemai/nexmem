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
| 1.5 (NEW) RLS coverage | ✅ | RLS enabled+forced on every memory table (m008) AND on `users` / `api_keys` / `token_usage` (m013) AND on `refresh_tokens` (m014) — see P2-C4 / P2-C5. SELECT on `users` and `api_keys` is permissive (login + key-hash lookup must work pre-auth); INSERT / UPDATE / DELETE are self-only. Cross-user mutation tests under `tests/integration/test_rls_users_apikeys_tokenusage.py`. |

---

## Phase 2 — Database Scalability and Performance

| Task | Status | Evidence |
| :--- | :---: | :--- |
| 2.1 PgBouncer / Connection Pooling | 🟡 | PgBouncer is in `docker-compose.yml` for local. **Not** in `render.yaml` (production); the path connects directly to the Supabase pooler. |
| 2.2 pgvector HNSW Indexing | ✅ | HNSW on `semantic_memory.vector` (m=16, ef_construction=200) and `engrams.dense_embedding` (m=16, ef_construction=64). |
| 2.3 Database Migrations | ✅ | 14 Alembic revisions; head is `014_refresh_tokens`. Migration 007 is non-destructive on already-correct schema (Phase 1, C5). Multi-replica startup is now race-safe via `scripts/run_migrations.py` + Postgres advisory lock (P2-C6). Manual hand-paste SQL files alongside Alembic still drift (R-M4) — Phase 3. |

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
| 4.3 Error Tracking (Sentry) | ✅ | Init wired when `SENTRY_DSN` is set. Sample rates default to 0.1 traces / 0.0 profiles (P2-C8); env-overridable via `SENTRY_TRACES_SAMPLE_RATE` / `SENTRY_PROFILES_SAMPLE_RATE`. Release tag `nexmem@0.1.0` for issue grouping. End-to-end smoke is an ops task. |
| 4.4 Cost / Token Tracking | 🟡 | `token_usage` table exists; rows logged via structlog. The `current_user.app_id` AttributeError that would have crashed every production /rag/chat is fixed (P2-C3 — log line uses `request.app_id`). Aggregation for billing is Phase 3. |

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
| 6.1 Unit & Integration Tests | ✅ | Unit (no external deps): 100 tests covering config safety, alembic env, migration 007, quota behaviour + wiring, app-scope consistency, episode-write atomicity, refresh-token security, lazy-loader singletons, observability redactor, secret scanner, locustfile target validation. Integration (Postgres + Redis service containers): auth flows, RLS isolation across memory + auth tables, quota enforcement, migrations, episode-write rollback, cross-app isolation, refresh-token rotation / logout / logout-all. Coverage is emitted as a CI artifact (P2-C9). |
| 6.2 Load Testing | 🟡 | `tests/locustfile.py` was POSTing to non-existent routes — **fixed in C10**. Now points at real routes; route-guard test in CI prevents regression. The locust run itself is not in CI. |
| 6.3 Security Audit (SAST) | 🟡 | `bandit` runs in CI. Currently fails on HIGH only; medium-severity findings accumulate silently. Add `pip-audit` and `npm audit` next. |

---

## What this PR (`backend/hardening-phase2`) added

- **P2-C1** Repo-wide secret scanner (`scripts/scan_secrets.py`) + CI gate
  + operator runbook for credential rotation and history rewrite.
- **P2-C2** Transactional `/memory/episode/write` (R-H1). Pre-DB failures
  raise 502/503; mid-DB failures propagate so `get_db` rolls back.
- **P2-C3** App scope consistency (R-H2 / R-H5). `current_user.app_id`
  AttributeError closed; engrams now carry `app_id` (migration 012);
  cross-app isolation tests added.
- **P2-C4** RLS on `users` / `api_keys` / `token_usage` (R-H7), with
  documented threat-model trade-offs in migration 013.
- **P2-C5** Refresh-token revocation (R-H11). New `refresh_tokens` table
  (migration 014), rotate-on-use `/auth/refresh`, `/auth/logout`,
  `/auth/logout-all`.
- **P2-C6** Migration race fix (R-H10). `scripts/run_migrations.py`
  takes a Postgres advisory lock around `alembic upgrade head`.
- **P2-C7** Cold-start hygiene (R-M3). Lazy loaders construct in
  worker threads; `WARM_MODELS_AT_STARTUP=true` in production.
- **P2-C8** Sentry sample rates default to 0.1 / 0.0 (R-H9);
  per-request structured `http_request` event; `redact_sensitive`
  scrubs known credentials before JSON render.
- **P2-C9** CI emits coverage; `CONTRIBUTING.md` documents the
  required branch-protection checks.
- **P2-C10** Honest reconciliation pass on this document and
  `PROJECT_STATUS.md`.

## What this PR (`backend/hardening-private-beta`, PR #1) added

- C1 — credential purge + repo-wide regression test.
- C2/C3/C4 — production fail-fast for `DEMO_MODE`, weak `SECRET_KEY`, wildcard CORS, and placeholder DB URLs.
- C5 — non-destructive migration 007.
- C6 — wired per-user monthly quota dependency on every write route.
- C7 — auth router branches on demo mode; "zero-dependency tests" claim is finally true.
- C8/C9 — real Postgres + Redis integration job in CI.
- C10/C12 — locustfile + stale-assertion fixes.

---

## What this PR did **not** ship (deferred to follow-up PRs)

The user-instructed scope of Phase 2 excluded billing, SDKs, MCP,
docs polish, and cosmetic refactors. The following items remain
open and are documented in
[`BACKEND_HARDENING_PHASE2.md`](./BACKEND_HARDENING_PHASE2.md) §3
and [`BACKEND_RISKS.md`](./BACKEND_RISKS.md):

- Billing pipeline (Stripe / Paddle) and customer-facing onboarding UI.
- API-key auth: set RLS GUC in middleware (R-H3 — fragility hardening).
- `apps.register_app` Pydantic body (R-H4).
- FK from memory tables to `users` with `ON DELETE CASCADE` (R-H6).
- Drop the `_generate_demo_reply` LLM-failure fallback (R-H8).
- Drop the per-process NetworkX graph (R-M1).
- Real DLQ for failed consolidation (currently a log line).
- pgaudit / append-only audit log.
- `pip-audit` / `npm audit` / Dependabot.
- Hand-paste SQL file consolidation (R-M4).

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
