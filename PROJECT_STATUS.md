# NexMem Project Status

**Last verified:** May 2026 — by direct code inspection on branch `backend/hardening-private-beta`.
**Status:** Alpha → Private Beta (in progress).
**Audience:** Investors, technical reviewers, contributors.

> This document is **honest**. Every status claim below is grounded in the
> code, not in intent. If you find a discrepancy between this doc and the
> repo, the repo is the source of truth — please open a PR.
>
> Companion documents:
> - [`REPO_STATE_AUDIT.md`](./REPO_STATE_AUDIT.md) — full investor-grade due-diligence audit.
> - [`BACKEND_HARDENING_PLAN.md`](./BACKEND_HARDENING_PLAN.md) — prioritized backend hardening backbone.
> - [`BACKEND_RISKS.md`](./BACKEND_RISKS.md) — living risk register (CRITICAL / HIGH / MEDIUM).
>
> Status legend: ✅ done · 🟡 partial · ❌ not started.

---

## 1. Project identity

NexMem is a persistent multi-tenant AI memory layer for LLM agents:
episodic / semantic / procedural / associative graph / engram memory exposed
via a versioned HTTP API and an MCP server. Two SDKs (`nexmem-py`,
`nexmem-js`) are in alpha.

---

## 2. Technology stack

### Backend
- **Framework:** FastAPI 0.115, Python 3.11.
- **Primary database:** PostgreSQL 16 + `pgvector` + `pg_trgm` + RLS (memory tables only — see §3).
- **Cache / broker:** Redis 7 (Celery broker, slowapi rate-limit storage, quota counters).
- **Background processing:** Celery 5.4 with tenacity retries.
- **Embeddings:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim) loaded locally.
- **Reasoning:** OpenAI `gpt-4o` / `gpt-4o-mini` for distillation and RAG.
- **NLP:** spaCy (`en_core_web_sm`) + NetworkX for the graph extractor.

### Frontend / clients
- **Marketing site:** `nexmem-landing/` — Next.js 16 / React 19, deployed to Vercel.
- **Ops dashboard:** `frontend/` — Streamlit, internal use only.
- **Python SDK:** `nexmem-py@0.1.0` — async + sync clients.
- **TypeScript SDK:** `nexmem-js@0.1.0`.
- **MCP server:** `nexmem-mcp@0.1.0` — four tools over stdio.

---

## 3. Feature implementation status

Reflects code **after** the backend hardening PR on branch
`backend/hardening-private-beta`.

| Feature | Status | Notes (code-verified) |
| :--- | :---: | :--- |
| Episodic memory | ✅ | `app/routers/episodic.py`; create / list / count / delete / consolidate. |
| Semantic memory | ✅ | `app/routers/semantic.py`; pgvector cosine + HNSW (m=16, ef=200). |
| Associative memory | ✅ | `app/routers/graph.py`; nodes / edges / BFS path / aggregate stats. |
| Procedural memory | ✅ | `app/routers/procedural.py`; JSONB settings + workflows; unique `(user_id, app_id)`. |
| Engram store | 🟡 | Engrams table works; lacks `app_id` column and FK to users (R-H5/H6 in risk register). |
| Hybrid retrieval | ✅ | RRF over vector + FTS + graph + cross-encoder rerank. |
| Background consolidation | 🟡 | Celery + tenacity retries; "DLQ" is a log line, not a real queue (R-H1, planned). |
| Multi-tenancy / RLS | 🟡 | RLS policies on the six memory tables (m008). `users`, `api_keys`, `token_usage` are **not** RLS-protected (R-H7). Engrams have no `app_id`. |
| GDPR compliance | 🟡 | `/export`, `/all` (hard delete with `X-Confirm-Delete`), `/consent` endpoints exist. No audit trail / soft delete. |
| Auth — JWT + API key | ✅ | `mem_*` keys SHA-256 hashed, constant-time verify. JWT HS256. |
| Auth — refresh tokens | 🟡 | Issued and accepted. **No** server-side revocation; lifetime 7 days. |
| Auth — production safety | ✅ | App refuses to start when `ENVIRONMENT=production` and `DEMO_MODE=true` / weak `SECRET_KEY` / wildcard CORS / placeholder DB host. |
| Quotas (per-user, monthly) | ✅ | `enforce_write_quota` dependency wired into `/episodes`, `/semantics`, `/episode/write`, `/rag/chat`. Fail-closed on broken Redis. Tier-based limits read from `settings`. Not yet integrated with billing. |
| Rate limit (per IP) | ✅ | slowapi 60/min default, Redis-backed when `REDIS_URL` set. |
| Login lockout | ✅ | Brute-force throttle on `/auth/login`. |
| Secrets management | 🟡 | Read from environment / Render dashboard. No Doppler / Vault / Secrets Manager integration. |
| Observability — logs | ✅ | structlog JSON; request-ID middleware. |
| Observability — metrics | 🟡 | `prometheus-fastapi-instrumentator` wired; `/metrics` token-protected; no Prometheus scrape yet verified. |
| Observability — errors | 🟡 | Sentry init when `SENTRY_DSN` set; trace/profile sample rates are 1.0 (cost bomb at any traffic — R-H9). |
| Token / cost tracking | 🟡 | `token_usage` table exists; rows logged via structlog; not yet aggregated for billing. |
| Health endpoints | ✅ | `/health/live` (process), `/health/ready` (DB + embedding service). |
| Migrations | 🟡 | 12-step Alembic chain. Migration 007 is now non-destructive on already-correct schema (gated behind `ALLOW_DESTRUCTIVE_MIGRATION=1`). Multiple manual SQL files alongside Alembic still drift from each other (R-M4). |
| Containerization | ✅ | Multi-stage Dockerfile, non-root user. |
| Continuous integration | ✅ | Unit job (lint + mypy + 56 demo-mode tests) + **real Postgres + Redis integration job** running auth, RLS, quota, and migration tests. Bandit security scan. |
| Backend deployment | 🟡 | Render blueprint defined. All four services on the **free** tier. PgBouncer is in `docker-compose.yml` but not in the Render path. |
| Frontend deployment | ✅ | Vercel via `.github/workflows/deploy-frontend.yml`. |
| Billing | ❌ | No Stripe / Paddle integration. Pricing page is marketing-only. |
| Onboarding UI | ❌ | No self-serve sign-up surface for end users. Streamlit dashboard is for ops. |
| Audit log | ❌ | No append-only log of admin/security-relevant events. |
| Webhooks / event bus | ❌ | None. |
| Data residency | ❌ | Single-region Supabase. |
| Org / team / RBAC | ❌ | One user = one tenant. |

---

## 4. Test coverage

### Today (after this hardening PR)

- **Unit job** (CI: `unit-tests`): 56 tests — config production safety, alembic env safety, migration 007 safety, quota behaviour, quota router-wiring, locustfile route guard, LLM service retries, and the demo-mode happy path. No external dependencies; runs on every PR.
- **Integration job** (CI: `integration-tests`): real Postgres (`pgvector/pgvector:pg16`) + real Redis (`redis:7-alpine`) service containers, applies Alembic migrations to head, then runs:
  - Auth: register persistence, duplicate-email rejection, login success/failure, `/me`, API-key lifecycle (create/list/use/revoke), refresh round-trip, invalid-refresh rejection.
  - RLS: cross-tenant list isolation; cross-user URL access returns 403; cross-user delete returns 403; raw-SQL probe of the policy with mismatched `app.current_user_id` GUC returns zero rows.
  - Quotas: free-tier 429 with structured payload; per-user counter isolation.
  - Migrations: alembic head reaches `011_fk_cascade_content_limits`; RLS enabled+forced on all six memory tables; migration 007 idempotent on already-correct schema.
- **Static security** (CI: `security-audit`): bandit on HIGH severity (medium pending, R-M7).

### Honestly missing

- ML-heavy paths (`RUN_ML_TESTS=1`): not in CI yet; require huggingface model download. Planned as a nightly job.
- Token revocation, scope enforcement, partial-write transactionality: tests will land with the H-priority fixes.
- Real load test: `tests/locustfile.py` is correct shape but not run in CI.

---

## 5. Database schema

- 12 Alembic revisions; head is `011_fk_cascade_content_limits`.
- HNSW indexes on `semantic_memory.vector` and `engrams.dense_embedding`.
- TSVECTOR + GIN on episodic and semantic FTS columns.
- RLS on episodic / semantic / procedural / knowledge_nodes / knowledge_edges / engrams (memory tables only — `users`, `api_keys`, `token_usage` are not yet RLS-protected; tracked as R-H7).
- FK CASCADE on `api_keys` and `token_usage` to `users` (m011).

Two parallel paths exist for applying migrations:
- `alembic upgrade head` (canonical).
- Hand-pasted SQL in `apply_migrations_supabase.sql`, `run_in_supabase_sql_editor.sql`, `verify_migrations.sql`, `supabase_migration_sql.sql` (drift signal — R-M4).

---

## 6. Recently fixed (this hardening PR)

| ID | What changed |
| :--- | :--- |
| C1 | Removed all hardcoded Supabase credentials from the repo (`alembic/env.py`, `scripts/*`, `render.yaml`). Whole-repo regression scan in `tests/test_alembic_env.py`. |
| C2 | `DEMO_MODE=true` now hard-fails in `production`. |
| C3 | Default / weak / `test-*` / `<32 char` `SECRET_KEY` now hard-fails in `production`. |
| C4 | Wildcard / empty `ALLOWED_ORIGINS` hard-fails in `production`. CORS middleware also forces `allow_credentials=False` whenever `*` is in the list. |
| C5 | Migration 007 is non-destructive on already-correct schema; destructive path requires `ALLOW_DESTRUCTIVE_MIGRATION=1`. |
| C6 | `enforce_write_quota` wired into the four write routes; fail-closed on broken Redis. |
| C7 | Auth router branches on `demo_mode` so the "zero-dependency" test claim is finally true. |
| C8/C9 | Real Postgres + Redis integration job in CI runs auth, RLS, quota, and migration tests. |
| C10 | `tests/locustfile.py` rewritten to target real routes; route-guard test added. |
| C12 | Stale service-name assertion fixed. |

---

## 7. Open technical debt (priorities, code-verified)

### Critical (should not ship to private beta without)

- **R-H1** Unified `episode/write` is not transactional; partial writes accepted on mid-flight failure.
- **R-H2** `current_user.app_id` `AttributeError` in `app/routers/rag.py` (User model has no `app_id`); will throw on every production `/rag/chat` after the LLM responds.
- **R-H7** Add RLS to `users`, `api_keys`, `token_usage`. (RLS today only covers memory tables.)
- **R-C1 (ops)** Rotate the leaked Supabase password; scrub git history.

### High (before public launch)

- **R-H3** API-key auth must set RLS GUC in `user_context_middleware`; today only Bearer JWT does.
- **R-H4** `apps.register_app` accepts query-string body params; replace with Pydantic body.
- **R-H5/H6** Engrams need `app_id` column; memory tables need FK to `users`.
- **R-H9** Drop Sentry sample rates from 1.0 to 0.1 / 0.0.
- **R-H10** Move `alembic upgrade head` out of the start command; add a Postgres advisory lock or release job.
- **R-H11** Add server-side refresh-token revocation.

### Medium (post-launch)

- **R-M2/M3** Per-process NetworkX graph and lazy ML-model loading on the request thread.
- **R-M4** Reconcile manual SQL files with Alembic.
- **R-M6** Enforce API-key scopes (currently advisory).
- **R-M7** Bandit on MEDIUM, plus `pip-audit` / `npm audit`.
- **R-M9** Schedule `cleanup_expired_episodic_memory()` via Celery beat.
- **R-M11** `/health/ready` should also probe Redis when configured.

The full register lives in [`BACKEND_RISKS.md`](./BACKEND_RISKS.md).

---

## 8. Environment configuration

Set via `.env` for local dev and via Render env vars / a secrets manager for production.

| Variable | Purpose | Example |
| :--- | :--- | :--- |
| `DATABASE_URL` | Async Postgres URL | `postgresql+asyncpg://u:p@host:5432/db` |
| `REDIS_URL` | Celery broker, slowapi, quota counters | `redis://localhost:6379/0` |
| `SECRET_KEY` | JWT signing (HS256). Must be ≥ 32 chars in production. | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `OPENAI_API_KEY` | LLM + embeddings | `sk-…` |
| `DEMO_MODE` | `false` in production. App refuses to start otherwise. | `false` |
| `ENVIRONMENT` | `development` or `production`. Production triggers strict validation. | `production` |
| `ALLOWED_ORIGINS` | Explicit comma-separated origins. `*` is rejected in production. | `https://nexmem.ai` |
| `SENTRY_DSN` | Optional — error tracking. | (Sentry project DSN) |
| `METRICS_SECRET_KEY` | Bearer token required for `/metrics`. | (random string) |
| `CONSOLIDATION_INTERVAL_MINUTES` | Celery beat cadence. | `30` |
| `ALLOW_DESTRUCTIVE_MIGRATION` | Required to consent to migration 007's destructive path. | `1` (only when knowingly required) |

---

## 9. What this codebase is *not* (yet)

To set expectations for reviewers:

- **Not a billable SaaS.** No payment provider integration.
- **Not multi-region.** Single-region Postgres + Redis.
- **Not enterprise-ready.** No SSO, MFA, audit log, DPA, or SOC-2 controls.
- **Not horizontally scaled.** NetworkX graph is per-process; multi-worker behaviour is undefined.
- **Not load-tested under realistic concurrency.** Locustfile shape is correct, no benchmark numbers are claimed.

It *is* a defensible, well-structured alpha with the production-safety
contract enforced at the code level, and an honest test suite. Use the
risk register and hardening plan to drive the next two weeks of work.
