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
| Engram store | ✅ | Engrams table now carries `app_id` (migration 012, P2-C3). FK-to-users still pending (R-H6). |
| Hybrid retrieval | ✅ | RRF over vector + FTS + graph + cross-encoder rerank. |
| Background consolidation | 🟡 | Celery + tenacity retries; "DLQ" is still a log line, not a real queue. |
| Multi-tenancy / RLS | ✅ | RLS enabled+forced on memory tables (m008) AND on `users` / `api_keys` / `token_usage` / `refresh_tokens` (m013, m014). Per-app scoping is application-layer at query time. |
| GDPR compliance | 🟡 | `/export`, `/all`, `/consent` endpoints exist. Hard delete only; no audit trail / soft delete. |
| Auth — JWT + API key | ✅ | `mem_*` keys SHA-256 hashed, constant-time verify. JWT HS256. |
| Auth — refresh tokens | ✅ | Server-side `refresh_tokens` table, rotate-on-use, `/auth/logout`, `/auth/logout-all` (P2-C5). |
| Auth — production safety | ✅ | App refuses to start when `ENVIRONMENT=production` and `DEMO_MODE=true` / weak `SECRET_KEY` / wildcard CORS / placeholder DB host. |
| Quotas (per-user, monthly) | ✅ | `enforce_write_quota` wired into 4 write routes. Fail-closed on broken Redis. Tier-based. Not yet integrated with billing. |
| Rate limit (per IP) | ✅ | slowapi 60/min default, Redis-backed when `REDIS_URL` set. |
| Login lockout | ✅ | Brute-force throttle on `/auth/login`. |
| Secrets management | 🟡 | Env-driven; whole-repo scanner + CI gate. No Vault / Doppler integration. |
| Observability — logs | ✅ | structlog JSON, request-ID middleware, per-request `http_request` event with request_id/method/path/route/status/latency/user_id/app_id, redactor scrubs known secrets (P2-C8). |
| Observability — metrics | 🟡 | `prometheus-fastapi-instrumentator` wired; `/metrics` token-protected; live scrape unverified. |
| Observability — errors | 🟡 | Sentry init when `SENTRY_DSN` set; sample rates default to 0.1 / 0.0 (P2-C8). |
| Token / cost tracking | 🟡 | `token_usage` table exists; rows logged via structlog with `app_id=request.app_id`; not yet aggregated for billing. |
| Health endpoints | ✅ | `/health/live`, `/health/ready` (DB + embedding service). |
| Migrations | ✅ | 14-step Alembic chain. Migration 007 is non-destructive on already-correct schema. `scripts/run_migrations.py` wraps `alembic upgrade head` in a Postgres advisory lock so multi-replica startup cannot race (P2-C6). |
| Cold-start safety | ✅ | Lazy loaders construct in worker threads, log warmup_complete elapsed_ms, expose `warmup()`. Production `WARM_MODELS_AT_STARTUP=true` (P2-C7). |
| Containerization | ✅ | Multi-stage Dockerfile, non-root user; CMD calls run_migrations.py. |
| Continuous integration | ✅ | secret-scan + unit-tests + real Postgres+Redis integration-tests + bandit. Coverage emitted; `CONTRIBUTING.md` documents required branch-protection checks. |
| Backend deployment | 🟡 | Render blueprint defined. Free tier across all four services. |
| Frontend deployment | ✅ | Vercel via `.github/workflows/deploy-frontend.yml`. |
| Billing | ❌ | No Stripe / Paddle integration. Pricing page is marketing-only. |
| Onboarding UI | ❌ | No self-serve sign-up surface for end users. |
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

## 6. Recently fixed

### Phase 2 (`backend/hardening-phase2`, this work)

| ID | What changed |
| :--- | :--- |
| P2-C1 | Repo-wide secret scanner (`scripts/scan_secrets.py`) and CI gate. Operator runbook `docs/SECRET_INCIDENT_RUNBOOK.md` covers Supabase password rotation, service-role rotation, history rewrite, force-push, collaborator notification. |
| P2-C2 | Transactional `episode/write` (R-H1). Embedder + engram errors raise 502/503 before any DB write; every DB INSERT propagates so `get_db` rolls back on any mid-flight failure. Live-DB rollback test included. |
| P2-C3 | App scope consistency (R-H2, R-H5). `current_user.app_id` AttributeError eliminated; engrams now carry `app_id`; cross-app isolation tests added. |
| P2-C4 | RLS extended to `users` / `api_keys` / `token_usage` (R-H7) with documented threat-model trade-offs. `deps.get_current_user` reordered so api_keys.last_used_at UPDATE happens after the GUC is set. |
| P2-C5 | Refresh-token revocation (R-H11). New `refresh_tokens` table with FORCE RLS; rotate-on-use `/auth/refresh`; new `/auth/logout` and `/auth/logout-all`. Pre-014 tokens are explicitly rejected. |
| P2-C6 | Migration race fix (R-H10). `scripts/run_migrations.py` wraps `alembic upgrade head` in a fixed-key Postgres advisory lock; Dockerfile + render.yaml use it. |
| P2-C7 | Cold-start hygiene (R-M3). Lazy loaders construct in worker threads, log `warmup_complete` with elapsed_ms, expose `warmup()`. New `WARM_MODELS_AT_STARTUP=true` is set on the production web service. |
| P2-C8 | Observability (R-H9, R-M10). Sentry sample rates default to 0.1 / 0.0, env-overridable. Per-request `http_request` structlog event with request_id / route / status / latency / user_id / app_id. New `redact_sensitive` processor scrubs known secrets before JSON render. |
| P2-C9 | CI truthfulness. pytest-cov on both jobs; coverage.xml uploaded. `CONTRIBUTING.md` documents the required branch-protection checks. |
| P2-C10 | Truth pass on docs (this update). |

### Phase 1 (PR #1)

| ID | What changed |
| :--- | :--- |
| C1 | Removed all hardcoded Supabase credentials from `alembic/env.py`, `scripts/*`, `render.yaml`. Whole-repo regression scan. |
| C2 | `DEMO_MODE=true` now hard-fails in `production`. |
| C3 | Default / weak / `test-*` / `<32 char` `SECRET_KEY` hard-fails in `production`. |
| C4 | Wildcard / empty `ALLOWED_ORIGINS` hard-fails in `production`; CORS forces `allow_credentials=False` whenever `*` is present. |
| C5 | Migration 007 is non-destructive on already-correct schema; destructive path requires `ALLOW_DESTRUCTIVE_MIGRATION=1`. |
| C6 | `enforce_write_quota` wired into the four write routes; fail-closed on broken Redis. |
| C7 | Auth router branches on `demo_mode` so the "zero-dependency" test claim is true. |
| C8/C9 | Real Postgres + Redis integration job in CI runs auth, RLS, quota, migration tests. |
| C10 | `tests/locustfile.py` rewritten to target real routes. |
| C11 | `PROJECT_STATUS.md` and `PRODUCTION_READINESS_PLAN.md` rewritten. |
| C12 | Stale service-name assertion fixed. |

---

## 7. Open technical debt (priorities, code-verified)

### Critical (should not ship to private beta without)

- **R-C1 (ops)** Rotate the leaked Supabase password; scrub git history.
  Phase 2 ships an automated scanner and a runbook; the rotation
  itself is operator-only. See `docs/SECRET_INCIDENT_RUNBOOK.md`.

### High (before public launch)

- **R-H3** API-key auth still does not set the RLS GUC inside
  `user_context_middleware`. The `deps.get_current_user` reorder in
  Phase 2 closed the silent-update path, but the middleware-vs-dep
  ordering remains a fragility surface.
- **R-H4** `apps.register_app` accepts query-string body params.
- **R-H6** Memory tables still lack FK to `users` (Phase 2 added
  engrams.app_id but cascading FKs are deferred — they require a
  controlled rollout against existing data).
- **R-H8** `_generate_demo_reply` masks LLM outages.

### Medium (post-launch)

- **R-M1** Per-process NetworkX graph still diverges across replicas.
- **R-M2** NLP semaphore is single-slot; bound to CPU count next.
- **R-M4** Reconcile manual SQL files with Alembic.
- **R-M5** `/metrics` Bearer compare uses `==`.
- **R-M6** API-key scopes are advisory only.
- **R-M7** Bandit on MEDIUM, plus `pip-audit` / `npm audit`.
- **R-M9** Schedule `cleanup_expired_episodic_memory()` via Celery beat.
- **R-M11** `/health/ready` should also probe Redis when configured.
- **R-M12** Per-IP / per-email throttle on `/auth/register`.

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
contract enforced at the code level, an honest test suite, and
documented incident response. Use the risk register and hardening
plan to drive the next two weeks of work.

## 10. Known limitations (Phase 2)

These trade-offs are deliberate; track each one in `BACKEND_RISKS.md`
if you want to see it raised in priority.

- **Access tokens are not server-revocable.** Refresh tokens are
  (P2-C5), so a stolen refresh token can be invalidated immediately
  via `/auth/logout` or `/auth/logout-all`. A stolen access token
  remains valid until its `exp`, default 4 h. Per-request DB lookup
  on access validation is deferred to Phase 3.
- **`api_keys` SELECT is permissive (RLS).** The key-hash auth flow
  must read the row before the user is identified; a row with
  `user_id`, name, and SHA-256 hash is not high-secret. UPDATE /
  DELETE / INSERT are still self-only.
- **`users` SELECT is permissive (RLS).** Login + key-hash auth must
  resolve email / id pre-auth. UPDATE / DELETE are self-only.
- **Engrams have `app_id` but memory tables still lack FKs to
  `users`.** GDPR `delete-all` is hand-coded; admin tooling that
  bypasses the route would leave orphans. Tracked as R-H6.
- **Cold-start models load on first request unless
  `WARM_MODELS_AT_STARTUP=true`.** Production env sets this; dev
  default is off.
- **NetworkX graph is per-process.** Multi-worker / multi-replica
  deployments diverge. Render free-tier single-worker is the
  current operating point.
