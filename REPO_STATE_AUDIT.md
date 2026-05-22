# REPO_STATE_AUDIT.md

**Project:** NexMem — Decentralized AI Memory Layer
**Reviewer:** Senior engineer / technical due-diligence (first-time pass)
**Method:** Direct code audit. No assumptions. "Unverified" is used wherever I could not confirm behavior from code alone.
**Repo state:** As cloned. Branch and HEAD not inspected.

---

## 1. Executive summary

NexMem is a FastAPI-based "memory-as-a-service" backend with five memory types (episodic, semantic, procedural, associative graph, engrams), a Streamlit ops dashboard, a Next.js marketing site, two SDKs (TS + Python, both v0.1.0 alpha), and an MCP server (v0.1.0 alpha). The product surface is unusually broad for a pre-seed/seed-stage codebase, and most of the *shape* of a production system is in place: routers, models, migrations, RLS policies, hybrid retrieval (RRF + cross-encoder rerank), Celery workers, Sentry/Prometheus hooks, structured logging, GDPR endpoints, and an MCP integration.

However, the project is **not** production-ready. The `PRODUCTION_READINESS_PLAN.md` checks every box, but the code disagrees with the plan in concrete ways:

- A live Supabase database **password is hardcoded in the repository** (`alembic/env.py`, `scripts/apply_migrations.py`). This must be assumed compromised.
- "Multi-tenant rate limiting + quotas" is described as complete; the quota function exists but is **not wired into any router**.
- "RLS enforcement" exists on memory tables only. `users`, `api_keys`, `token_usage` have no RLS.
- CI runs only `DEMO_MODE=true` tests against in-memory stores. Real database, real auth, and real RLS are exercised by **zero tests in CI**. The DB and ML test suites are gated behind env vars that the CI workflow never sets.
- A migration in the chain (`007_standardize_vector_dim`) `DELETE FROM semantic_memory` on `upgrade()` with no warning. Re-running migrations against a populated DB will silently destroy production data.
- There is no billing layer. The `tier` column and `token_usage` table exist; nothing reads them to enforce or invoice.
- The Streamlit/Next.js front ends are mostly visual; no end-user auth UI exists outside of pasting a token into a Streamlit text input.
- Render is configured on the **free tier for every service** (web, worker, beat, redis), which is incompatible with the SLA implied by the marketing site.

The system is closer to "credible alpha" than "shippable beta." It is a strong technical demo and a defensible architecture story, but it is not yet defensible against a hostile customer, a real auditor, or a 10× traffic spike.

---

## 2. Repo structure

Top-level (verified):

```
/projects/sandbox/nexmem
├── app/                      FastAPI backend
│   ├── main.py               app bootstrap, lifespan, /metrics, root, stats endpoints
│   ├── config.py             pydantic-settings, demo_mode flag, CORS parser
│   ├── database.py           async SQLAlchemy + RLS contextvar + after_begin hook
│   ├── celery_app.py         Celery + beat schedule (consolidate every 30 min)
│   ├── tasks.py              consolidate_user_memory_task, consolidate_all_users
│   ├── demo_db.py            in-memory dict stores used when DEMO_MODE=true
│   ├── core/                 deps.py, security.py, rate_limit.py, rate_limit_redis.py,
│   │                         brute_force.py, logging.py
│   ├── middleware/           logging.py
│   ├── models/               user.py (User, APIKey, TokenUsage), memory.py, engram.py
│   ├── routers/              auth, memory, episodic, semantic, procedural, graph,
│   │                         rag, apps, gdpr, health
│   ├── schemas/               pydantic request/response models
│   └── services/              embedder, llm, retriever, reranker, consolidation,
│                              engram_processor, scheduler, app_registry
├── alembic/                   12 revisions; first revision is a no-op baseline
├── supabase/migrations/       2 raw SQL migrations (legacy hand-applied path)
├── apply_migrations_supabase.sql, run_in_supabase_sql_editor.sql,
│   verify_migrations.sql, supabase_migration_sql.sql
│                              4 hand-paste migration scripts (drift signal)
├── tests/                     8 pytest files + locustfile.py + run_security_audit.py
├── scripts/                   seed_demo, apply_migrations, check_health,
│                              clear_keys, demo_walkthrough, migrate_to_uuid,
│                              download_spacy_model
├── frontend/                  Streamlit dashboard (app.py)
│   └── App.tsx                stale legacy file used by build_page.py
├── nexmem-landing/            Next.js 16 + React 19 marketing site (single page)
├── nexmem-js/                 TypeScript SDK v0.1.0
├── nexmem-py/                 Python SDK v0.1.0 (async + sync clients)
├── nexmem-mcp/                MCP server v0.1.0 (4 tools)
├── Dockerfile, docker-compose.yml, docker-compose.prod.yml
├── render.yaml                Render blueprint (free tier across the board)
├── .github/workflows/         ci.yml, deploy-frontend.yml
├── DEPLOY.md, README_DEPLOY.md, PROJECT_OVERVIEW.md, PROJECT_STATUS.md,
│   PRODUCTION_READINESS_PLAN.md, README.md
├── requirements.txt, requirements-dev.txt, pytest.ini, alembic.ini
└── build_page.py, deploy.py, deploy.sh, test_write.py
```

Surface area is large; ownership and discipline appear to be a single-author cadence with iterative cleanup.

---

## 3. Built features

These are **verified** as present in code and reachable through the API:

- **FastAPI app** with lifespan, JSON logging (`structlog`), Prometheus instrumentation (token-gated `/metrics`), Sentry init when DSN is set, request-ID middleware, CORS middleware.
- **Auth**:
  - Email/password registration + login.
  - JWT access tokens (HS256, 4h default) + refresh tokens (7d default).
  - API keys (`mem_…`, SHA-256 hashed, `secrets.compare_digest` verify).
  - `/auth/me`, `/auth/api-keys` CRUD.
  - Brute-force lockout on login (5 fails / 10-min window / 15-min lockout, Redis or in-process).
- **Memory routers**:
  - Episodic: create / list / count / delete / consolidate, with `app_id` query param.
  - Semantic: create / list / count / vector search via pgvector cosine `<=>`.
  - Procedural: get / upsert / delete with unique `(user_id, app_id)`.
  - Graph: nodes / edges / BFS path / SQL-aggregate stats.
  - Engram: read by id.
  - Unified write: `POST /api/v1/memory/episode/write` (writes episodic + semantic + engram + graph in one call).
  - Unified context: `POST /api/v1/memory/context` (assembles engram + semantic + episodic + preferences + graph context).
- **RAG chat**: `/api/v1/rag/chat` with hybrid retrieval (RRF over vector + keyword + graph) and cross-encoder rerank, falling back to a keyword-based fake reply on LLM failure.
- **Apps**: register / list / revoke / per-app stats. App identity is encoded as a scope string `app:<uuid>` in the API key's `scopes` column (no dedicated `apps` table).
- **GDPR**: `/memory/user/{id}/export`, `/memory/user/{id}/all` (delete-all w/ `X-Confirm-Delete: true`), `/memory/user/{id}/consent`.
- **Health**: `/health/live`, `/health/ready` (DB + embedding service).
- **Background processing**: Celery worker + beat scheduling consolidation every 30 min; tenacity retries; verbose log on permanent failure (DLQ in name only — see §5).
- **Database**: 12-step Alembic chain + 2 Supabase SQL migrations; pgvector with HNSW indexes for semantic and engram tables; FTS tsvectors with GIN indexes; RLS on memory tables; FK cascades on `api_keys` and `token_usage` (added in 011).
- **NLP/ML**: spaCy `en_core_web_sm`, sentence-transformers `all-MiniLM-L6-v2` (384D), cross-encoder `ms-marco-MiniLM-L-6-v2`, NetworkX graph.
- **SDKs**:
  - `nexmem-js@0.1.0`: `MemoryClient` with `remember`, `recall`, `setProfile`, `getProfile`, `link`, `forgetAll`, `export`.
  - `nexmem-py@0.1.0`: async `MemoryClient` and `SyncMemoryClient` with the same surface.
- **MCP**: `nexmem-mcp@0.1.0` exposing `nexmem_remember`, `nexmem_recall`, `nexmem_search`, `nexmem_set_profile` over stdio.
- **Frontend**: Streamlit dashboard (3-column glassmorphism UI), Next.js landing site (single-page marketing).
- **CI**: `.github/workflows/ci.yml` runs flake8 + mypy + pytest (demo mode) + bandit + Docker build (no push). Frontend deploys to Vercel via separate workflow.
- **Deployment**: Render blueprint defines `web`, `worker` (Celery), `worker` (beat), and `redis` services.

---

## 4. Incomplete features

These features are present in code but **partial, inconsistent, or not wired through end-to-end**:

- **Multi-tenant quotas / rate limiting**:
  - `app/core/rate_limit_redis.py` defines `check_quota` and `rate_limit_middleware`.
  - **Neither is imported or used by any router.** The only active limiter is the global `slowapi` 60/min IP limit in `app/core/rate_limit.py`. Tier-based monthly quotas advertised in `config.py` (`free_monthly_writes` etc.) are not enforced.
- **App registry**:
  - There is no `apps` table. `register_app` creates an `APIKey` row whose `scopes` field embeds `app:<uuid>`.
  - `validate_app_access` checks scope strings, not relationships. App listing, revocation, and stats all key off the API key, not a real app entity.
  - `register_app` accepts `app_name` and `description` as **query string parameters**, not a Pydantic body.
- **Engram completeness**:
  - `engrams` table has no `app_id` column and no FK to `users` (verified in `app/models/engram.py` and `supabase/migrations/002`). Engrams cannot be safely scoped per app, and orphaned engrams are possible if a user is deleted.
  - GDPR delete loops through engrams via `delete(Engram).where(Engram.user_id == user_id)`, but there is no FK enforcing this on the DB side.
- **NetworkX graph state**:
  - The in-memory graph is rebuilt at startup from `knowledge_edges` (`rebuild_networkx_graph` in `main.py`).
  - This is **per-process**. Multi-worker / multi-replica deployments will have divergent in-memory graphs. There is no shared cache or cross-process invalidation.
  - `LazyEngramProcessor.get_compressed_context` is called from a synchronous code path inside the `/memory/context` endpoint, which can lazily load the heavy spaCy + sentence-transformers + cross-encoder models on the request thread.
- **RAG fallback**:
  - On any LLM error, `_generate_demo_reply` returns a hardcoded if/else string ("Hello! I'm your AI assistant…"). This will mask real outages from a customer's perspective: they get a confident reply that has nothing to do with their data.
- **Demo mode coexistence**:
  - Every memory router has a `if settings.demo_mode:` branch returning fixtures from `app/demo_db.py`. The auth dependency itself **bypasses the Authorization header** in demo mode and synthesizes a single `DEMO_USER_ID` user. If `DEMO_MODE=true` is ever set in production, the entire auth layer is silently disabled.
- **Migration tooling**:
  - `apply_migrations_supabase.sql`, `run_in_supabase_sql_editor.sql`, `verify_migrations.sql`, and `supabase_migration_sql.sql` are hand-paste scripts that overlap with `alembic/versions/*`. `apply_migrations_supabase.sql` even contains invalid SQL (`REFERENCES NULL`). This is a strong drift signal: migrations are being patched manually in Supabase and Alembic is being kept happy after the fact.
- **CI test gating**:
  - `tests/test_auth.py`, `tests/test_memory.py`, `tests/test_memory_context.py`, `tests/test_engram_processor.py` all skip unless `RUN_DB_TESTS=1` or `RUN_ML_TESTS=1`. The CI workflow does not set either. Effective coverage in CI = demo-mode happy paths only.
- **Locust load test**:
  - `tests/locustfile.py` posts to `/api/v1/episodic/` and `/api/v1/rag/chat` with no `user_id` in the path. Neither route shape exists in the current routers (actual is `/api/v1/agents/{user_id}/episodes`). The load test will produce 404 / 422 noise that looks like load.
- **Front-end auth**:
  - The Streamlit dashboard is a developer console; it accepts a free-text `Auth Token` field. There is no register/login UI, no API-key management UI, no signup flow.
  - The Next.js landing site has CTAs that open `localhost:8501` (set via `NEXT_PUBLIC_DASHBOARD_URL` env var). No real onboarding flow.
- **Sentry / Prometheus / structlog**: integrations are wired but there is no dashboard, alert routing, or ingest verified from code.

---

## 5. Missing features

Verified absent from the codebase:

- **Billing**: No Stripe / Paddle / billing provider integration. No subscription model. No invoice/usage export. No webhook handlers for payment events. The `Pricing` section in the landing site is marketing-only.
- **Quota enforcement**: As noted above, quotas exist on paper only.
- **Audit log**: No `audit_log` table, no append-only log of admin/security-relevant actions (key creation, key revoke, GDPR delete, login, password change). `last_used_at` on API keys is the only audit signal.
- **Webhooks / outbound integrations**: No webhook subsystem, no event bus, no SSE/streaming endpoints.
- **Background DLQ**: `consolidate_user_memory_task` logs `"DLQ [DEAD LETTER QUEUE]"` after `max_retries`, but there is no actual dead-letter queue. The message is dropped after a log line.
- **Data residency / region pinning**: No tenant-region binding; all data goes to a single Supabase project (Tokyo `aws-1-ap-northeast-1` per `render.yaml`). For EU customers with data-residency requirements, this is a non-starter.
- **Encryption at rest at the application layer**: pgvector + Supabase encryption only.
- **Encryption in transit between services**: SSL to Postgres is enforced (`ssl=require`), but Redis between Render services is unconfigured for auth/TLS in `docker-compose.prod.yml` (`redis://redis:6379/0`).
- **Soft delete / GDPR audit trail**: `delete-all` is hard delete, no tombstone, no proof-of-deletion record.
- **Onboarding / signup UI**: No web UI for the most basic flow ("get an API key").
- **Token revocation list**: JWT refresh tokens are signed but there is no revocation. A leaked refresh token is valid for 7 days; only changing `SECRET_KEY` invalidates everything.
- **MFA / SSO / password reset**: No 2FA, no SSO, no email-based password reset. `wallet_address` login is in the schema but no challenge/verify code path was found.
- **Fine-grained scopes**: API key `scopes` is a comma-separated string with no enforcement (`read,write` is set on every key; no router checks scopes).
- **Org / team / RBAC**: No multi-user organizations. One user = one tenant. No invite flow.
- **Vector embedding versioning**: `embedding_model` column exists on semantic rows, but there is no rebuild/migration path when the model changes.
- **Backfill / replay tools**: Consolidation can be triggered, but there is no admin tool to selectively re-run engram extraction or rebuild graphs.
- **Backups / disaster recovery**: Not documented. Supabase free tier defaults are assumed.
- **Documentation site / API reference**: No published API docs. FastAPI `/docs` (Swagger) is the only reference. SDK READMEs are short.
- **Status page / public SLO**: None.
- **Versioned API**: All routes are under `/api/v1/`, but there is no concept of API deprecation, sunset headers, or version negotiation.
- **User-facing dashboard**: Streamlit is for ops, not for paying users.

---

## 6. Backend / API audit

**App bootstrap (`app/main.py`)**:
- CORS uses `settings.allowed_origins` with `allow_credentials=True`. Default is `["*"]` and `validate_production` only logs a warning. With credentialed CORS, `*` is silently downgraded by browsers, but a misconfigured production deploy still gets the worst of both worlds.
- Background graph rebuild is `asyncio.create_task` with a 120-second timeout. Failure is logged at `WARNING` and swallowed; the API serves traffic regardless.
- `user_context_middleware` decodes JWT itself (independent of `get_current_user`) to set the `app.current_user_id` PostgreSQL session variable for RLS. **API-key auth is not handled here**, only Bearer JWT. Verified by reading the middleware:
  ```
  if scheme.lower() == "bearer":
      payload = jwt.decode(...)
      user_id = payload.get("sub")
  ```
  This means: requests authenticated with `Authorization: ApiKey …` will leave `app.current_user_id` unset for the lifetime of the middleware, and RLS will only be applied later when `get_current_user` runs `set_rls_context` in the dependency. There is a window where DB queries can run without RLS context. **Unverified** in practice (depends on dependency resolution order in FastAPI), but worth a real test.

**Routers**:
- Path-based `user_id` is validated against authenticated user (`if str(current_user.id) != user_id: raise 403`). Good.
- `apps.register_app` accepts `app_name` and `description` as query string params. This is technically a security defect (POST body should not be query-stringed) and breaks tools that strip query strings from POSTs. Body should be Pydantic.
- `rag_chat` swallows LLM exceptions and returns a hard-coded "Hello / Based on your stored preferences …" reply on failure. Customers cannot distinguish a real outage from a fluke response.
- `memory.write_episode` writes to four tables in sequence with **per-step `try/except`**, so partial writes are accepted. There is no transaction enclosing the four writes (`db.commit()` is implicit at the end of `get_db`). On a mid-flight failure, you can have a stored episodic + missing semantic + missing engram + dangling graph nodes.
- Pagination is offset-based on graph endpoints; no cursor pagination anywhere.
- No idempotency keys on writes.
- Validation: max content 32 768 chars, max query 4 096 chars; matches DB CHECK from migration 011.

**Observability**:
- `/metrics` returns `503` if `METRICS_SECRET_KEY` is unset (good). Bearer-token check is a constant-time string compare via Python `==`, not `secrets.compare_digest` (timing leak; minor).
- `prometheus_fastapi_instrumentator` is invoked but the instrumentator is **not exposed**. Metrics gathered by the instrumentator are not surfaced (`expose=False` is the default; the custom `/metrics` endpoint uses `prometheus_client.generate_latest()` against the global registry, which **does** include instrumentator metrics — verified, OK).
- Sentry init only runs when `SENTRY_DSN` is set. No release/version tagging. Trace + profile rates set to 1.0 — **wildly expensive** at any traffic.

---

## 7. Database / migrations audit

**Schema** (verified from `app/models/*.py`):

| Table | Has app_id | Has user_id FK | RLS |
|---|---|---|---|
| `users` | n/a | n/a | ❌ |
| `api_keys` | ❌ | ✅ (CASCADE, m011) | ❌ |
| `token_usage` | string only | ✅ (CASCADE, m011) | ❌ |
| `episodic_memory` | ✅ | ❌ (no FK to users) | ✅ (m008) |
| `semantic_memory` | ✅ | ❌ (no FK to users) | ✅ (m008) |
| `procedural_memory` | ✅ (unique on (user_id, app_id)) | ❌ | ✅ (m008) |
| `knowledge_nodes` | ✅ | ❌ | ✅ (m008) |
| `knowledge_edges` | ✅ (m006) | ❌ | ✅ (m008) |
| `engrams` | ❌ | ❌ | ✅ (m008) |

Findings:

- **No FK from any memory table to `users.id`**. User deletion only cleans up via the GDPR endpoint's explicit `DELETE` statements. Direct deletion of a user (admin task, RLS bypass) leaves orphaned memories.
- **Engram table lacks `app_id`**. Multi-app scoping is leaky for engrams.
- **RLS only covers memory tables**. `users`, `api_keys`, `token_usage` are unrestricted by RLS. Any session that can run SQL and bypass auth (e.g., the Supabase service role key, or a code path that doesn't go through `get_current_user`) can read all keys and emails.
- **RLS depends on `app.current_user_id`** being set via `set_config('app.current_user_id', :uid, true)`. The session-level setting is set:
  - In a SQLAlchemy `after_begin` hook (each transaction).
  - In the JWT-decode HTTP middleware.
  - In `set_rls_context` from the auth dependency.
  Three independent code paths must agree. If any one fails to run for a query, `current_setting('app.current_user_id', true)` returns `''`, `NULLIF` returns `NULL`, the `USING (user_id = NULL)` predicate is false, and the query returns zero rows. This **fails closed** for reads, which is the right default. It does mean any "RLS broken" symptom looks like "empty results" and not "blocked", which complicates operations.
- **Connection mode**: `prepared_statement_cache_size=0`, `statement_cache_size=0`, `ssl=require`. This is correct for PgBouncer transaction-pooling.

**Migration chain** (`alembic/versions/`):

```
001_baseline (no-op, just CREATE EXTENSION)
  ↓
51d59ebea874 (Day 3 auth, no-op)
  ↓
002_hnsw_index
  ↓
003_add_consolidated_at
  ↓
004_add_fts_columns
  ↓
005_add_app_id_procedural
  ↓
006_align_app_scoping
  ↓
007_standardize_vector_dim   ← UPGRADE() RUNS `DELETE FROM semantic_memory`
  ↓
008_enable_memory_rls
  ↓
009_engram_hnsw_index
  ↓
010_token_usage
  ↓
011_fk_cascade_content_limits
```

Findings:

- **Migration 007 is destructive**. `op.execute("DELETE FROM semantic_memory")` on `upgrade()`. There is no `if vector_dim != 384: skip` guard. Any environment that runs `alembic upgrade head` from a state earlier than 007 will lose all semantic memory rows.
- **Schema drift between Alembic and Supabase**:
  - Supabase `001_initial_schema.sql` declares `VECTOR(1536)` and uses `text-embedding-3-small`.
  - Alembic `007` standardizes to `VECTOR(384)` and `all-MiniLM-L6-v2`.
  - README still describes 1536-dim. Inconsistent documentation increases the chance of an operator picking the wrong path.
- **Manual SQL files in repo**: `apply_migrations_supabase.sql`, `run_in_supabase_sql_editor.sql`, `verify_migrations.sql`, `supabase_migration_sql.sql`. The first contains `REFERENCES NULL` (invalid SQL). Existence of these files is a signal that migrations are being applied by hand and reconciled afterward.
- **`Dockerfile` runs `alembic upgrade head` on container start**. With `--workers` > 1 or multiple replicas, there is no migration lock — multiple workers race the migration runner.
- **`alembic/env.py` has a fail-safe override** that hardcodes a specific Supabase pooler URL **including the URL-encoded password** when the configured DATABASE_URL "looks stale." See §8.

---

## 8. Security audit

This section is the most concerning part of the audit.

**Hardcoded credentials in the repository (verified)**:

- `alembic/env.py`:
  ```python
  if "db.***REDACTED_PROJECT_ID***" in database_url or not database_url:
      database_url = "postgresql://postgres.***REDACTED_PROJECT_ID***:***REDACTED_PASSWORD***@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
  ```
- `scripts/apply_migrations.py`:
  ```python
  DB_URL = "postgresql://postgres:***REDACTED_PASSWORD***@db.***REDACTED_PROJECT_ID***.supabase.co:5432/postgres"
  ```
- `render.yaml` hardcodes the Supabase pooler hostname and project ref (`***REDACTED_PROJECT_ID***`) in plaintext (no password embedded).

**Action required**: assume the password `***REDACTED_PASSWORD***` (URL-decoded) is compromised. Rotate immediately, scrub git history, and confirm no other instances of the secret remain.

**Authentication design**:

- JWT HS256 with `SECRET_KEY`. Default `SECRET_KEY` is `local-dev-secret-change-this-before-production`. `validate_production` warns but does **not** raise on weak/default secret. The original raise was removed: a comment in `config.py` says *"Removed the RuntimeError raise to prevent startup hangs."* This means a misconfigured production deploy with the default secret will start, log a warning, and accept forged JWTs.
- API keys: `mem_` prefix + 32 random URL-safe bytes; SHA-256 stored, constant-time verify. Good.
- Refresh tokens: signed JWTs, no revocation.
- No MFA, no SSO, no password reset, no email verification.
- `auth/register` is not rate-limited beyond the global slowapi 60/min; brute-force lockout only protects `/auth/login`.
- `validate_app_access` matches scope substrings to determine ownership; not a real authorization decision.

**Authorization**:

- Path-`user_id` checks enforce per-user isolation in routers (`if str(current_user.id) != user_id: 403`). Good.
- RLS is the deeper defense, but only for memory tables.
- API key `scopes` field is set to `read,write` for every key and never enforced.
- Demo-mode bypass: `if settings.demo_mode: return synthetic_user`. **Production must guarantee `DEMO_MODE=false`.** The `render.yaml` correctly sets this; nothing in code enforces it.

**Transport / network**:

- `ssl=require` for asyncpg. Good.
- Redis URL in `docker-compose.prod.yml` is plain `redis://`, no auth, no TLS. For a self-hosted prod deploy this is critical; for Render's managed Redis, the connection string is opaque.
- CORS default = `["*"]` with `allow_credentials=True`. Browsers will downgrade, but a misconfigured deploy is still dangerous (e.g., for non-browser clients).
- HSTS, CSP, security headers: not present (would normally be done at the edge).

**Input validation**:

- 32 KB content limit, 4 KB query limit. Enforced both in Pydantic and in DB CHECK constraints (m011). Good.
- No file uploads, no webhook ingest, so attack surface is mostly text + JSON.
- Pydantic schema for `apps.register_app` is missing — query-string params bypass validation.

**Logging / telemetry**:

- Structlog JSON output. Good.
- Request ID injected per request.
- `/metrics` token-protected (string `==` compare; minor timing-attack risk).
- Sentry integrated; sample rates `traces_sample_rate=1.0`, `profiles_sample_rate=1.0` will be expensive and noisy at scale.

**SAST in CI**:

- `bandit` runs at `--severity-level medium` and writes a JSON report. The CI step **fails only on HIGH** issues. Medium-severity findings are reported and ignored.

**No dependency scanning** (no Dependabot/Renovate, no `pip-audit`, no `npm audit` in CI).

**Other**:

- `app/services/llm.py:track_token_usage` uses `from asgiref.sync import async_to_sync` to write inside a sync OpenAI handler. This will deadlock if called from a running event loop. It is currently called via `asyncio.to_thread`, which is fine, but the pattern is fragile.
- `engram_processor.py` mutates a shared `_user_contexts` dict from sync code; the lazy lock is only used for first-init; concurrent `process_async` calls update graph state without a per-user lock.
- Email enumeration is partially mitigated (login responds with the same `_INVALID` message and records a failure for unknown emails), but timing differences from the bcrypt verify step on real users vs. nonexistent ones may still be observable.

---

## 9. Performance / reliability audit

**Cold-start cost**:
- spaCy `en_core_web_sm` (≈ 50 MB), sentence-transformers `all-MiniLM-L6-v2` (≈ 90 MB), cross-encoder `ms-marco-MiniLM-L-6-v2` (≈ 90 MB) are loaded lazily on first use, **inside the request thread**, with a single semaphore. First `/memory/context` or first `/rag/chat` after deploy will block until all three models are downloaded and loaded. Render free tier cold starts can stack with this.
- `Dockerfile` does not pre-download these models; they are pulled at runtime.

**Latency**:
- pgvector HNSW indexes are correctly created (m=16, `ef_construction=64` and 200). Search performance should be acceptable up to mid-millions of vectors. **Unverified** — no benchmark in the repo.
- The `_generate_demo_reply` fallback hides LLM latency spikes by returning a hard-coded reply.
- `process_async` runs on `asyncio.to_thread` with a single shared semaphore (`Semaphore(1)`), serializing all NLP/embedding work across the entire process. Concurrent writers will queue.

**Scalability**:
- In-process Slowapi limiter (`storage_uri = settings.redis_url or "memory://"`). If `REDIS_URL` is set, it is shared. Otherwise it is per-process and useless behind multiple workers.
- NetworkX graph is per-process; not shared between workers.
- `--workers 1` (Render web) vs. `--workers 2` (`docker-compose.prod.yml`). Inconsistent.
- Celery `consolidate_all_users` runs every 30 min and enqueues a task per user. With many users this becomes a thundering herd against OpenAI.
- No backpressure on writes. A single client can push as many `episode/write` calls as the global rate limit allows.

**Reliability**:
- Sentry is set up but not verified.
- No graceful shutdown handling for in-flight Celery tasks beyond Celery defaults.
- `consolidate_user_memory_task` claims a DLQ but only logs on permanent failure. Lost work is not recoverable.
- The unified write path is not transactional; partial writes are possible (see §6).

**Storage**:
- Episodic memory has a configurable cleanup function (`cleanup_expired_episodic_memory`), but the trigger is manual (`POST /api/v1/memory/cleanup`). No scheduled job calls it.

**Capacity headroom (unverified)**:
- Supabase free tier: 500 MB DB, 2 GB egress/month. Not a serious customer load.

---

## 10. Testing audit

**File inventory** (`tests/`):
- `conftest.py` — autouse demo-store reset; `auth_headers`, `demo_user_id` fixtures; forces `DEMO_MODE=true`.
- `test_isolation_and_write.py` — happiest path; relies entirely on demo mode.
- `test_llm_service.py` — mocks OpenAI, validates retries, exercises in-memory `demo_db` directly.
- `test_engram_processor.py` — gated by `RUN_ML_TESTS=1` (not set in CI).
- `test_concurrent_writes.py` — exercises `episode/write` 10× with `asyncio.gather` in demo mode.
- `test_auth.py` — gated by `RUN_DB_TESTS=1` (not set in CI).
- `test_memory.py` — gated by `RUN_DB_TESTS=1`. Asserts `data["service"] == "Decentralized AI Memory Layer"`, but actual root returns `"NexMem - Decentralized AI Memory Layer"`. Even when run, the test will fail.
- `test_memory_context.py` — gated by `RUN_DB_TESTS=1`.
- `locustfile.py` — references `/api/v1/episodic/` and a body-shape that the actual route rejects.
- `run_security_audit.py` — runs bandit; treats medium severity as informational.

**CI**:
- `lint-and-test` job runs flake8 syntax-fail + soft warnings, mypy with `|| true`, pytest in demo mode.
- `security-audit` job depends on `lint-and-test` and runs `tests/run_security_audit.py`, which only fails on HIGH-severity bandit findings.
- `docker-build` job builds (does not push).

**Coverage of risky behaviors in CI** (verified by tracing):
- RLS: not exercised (no DB).
- Auth user-isolation against real DB: not exercised.
- pgvector search correctness: not exercised.
- Migrations: not exercised end-to-end (no Postgres in CI).
- Rate limiting / quotas: not exercised.
- Concurrency under load: not exercised.

**Effective CI gives**: syntax + happy-path demo-mode behavior. It does not protect against database, auth, or RLS regressions.

---

## 11. Deployment audit

**Render blueprint (`render.yaml`)**:
- Four services on the **free** plan: `nexmem-api` (web), `nexmem-celery-worker`, `nexmem-celery-beat`, `nexmem-redis`. No paid plan in repo. Free Render web services sleep after inactivity; the dashboard's "always-on" promise is not met.
- `DATABASE_URL` hardcoded to a specific Supabase pooler hostname (no password). Project ref `***REDACTED_PROJECT_ID***` is exposed.
- `SECRET_KEY` uses `generateValue: true`. Good.
- `OPENAI_API_KEY`, `SENTRY_DSN`, `METRICS_SECRET_KEY` are `sync: false` (set manually). Good.
- `DEMO_MODE: "false"`. Good.
- `CONSOLIDATION_INTERVAL_MINUTES: "30"`.
- `ALLOWED_ORIGINS: "https://nexmem.onrender.com,https://nexmem.ai"`. Good.

**Docker**:
- `Dockerfile` is multi-stage, non-root user `appuser`, exposes 8000, runs `alembic upgrade head` then `uvicorn`. Good baseline.
- `docker-compose.yml` uses PgBouncer for local; `docker-compose.prod.yml` does not. Neither is used by the Render path (Render runs python directly per `render.yaml`'s `buildCommand`, not the Dockerfile).
- Multi-replica deployments will race the migration step.

**Front-end deploy**:
- `deploy-frontend.yml` deploys `nexmem-landing/` to Vercel on push to `main`. Standard Vercel + Next.js flow.

**Other tooling**:
- `deploy.sh` and `deploy.py` are interactive scripts intended for manual local/Render bootstrap. They embed default `.env.production` content with placeholders. They are not invoked by CI.

**Environments**:
- No documented or wired staging environment. There is one "production" environment (Render free tier) and developer laptops.

---

## 12. Launch blockers

These prevent a credible public launch with paying customers:

1. **Hardcoded Supabase password** in `alembic/env.py` and `scripts/apply_migrations.py`. Must be rotated and scrubbed from git history.
2. **Migration 007 destroys data** on upgrade. Any new environment or recovery scenario will silently delete `semantic_memory`. Must be guarded.
3. **`DEMO_MODE` auth bypass** is a config-flag-distance away from total auth disable. Hard-fail when `DEMO_MODE=true` and `ENVIRONMENT=production`. Tests and `render.yaml` already set `DEMO_MODE=false`, but there is no defence-in-depth.
4. **No quota / rate-limit enforcement** on writes per app/user. Free tier customers can hit the OpenAI bill.
5. **No billing**. Customers have no way to pay.
6. **No signup / onboarding UI**. Self-serve is impossible without a developer pasting tokens.
7. **CORS default `*` + `allow_credentials=true`** in code; production override is documented but not enforced.
8. **JWT secret weak-secret check is a warning** (was raised, then downgraded). Hard-fail on weak secret in production mode.
9. **CI does not exercise the database, RLS, or auth** at all. Regressions to these areas land silently.
10. **Stale tests** (`test_memory.py` service-name assertion) and **broken Locust file** indicate the test suite isn't being kept honest.
11. **Render free tier** for web + worker + beat + redis. Beat must always run for consolidation; free workers can sleep / be killed.
12. **Hardcoded fallback Supabase project ref** in `render.yaml` and `alembic/env.py`. A different customer / environment can't reuse this blueprint without editing.

---

## 13. Scale blockers

These prevent growing past a few dozen active customers:

1. **In-memory NetworkX graph per process**. Multi-worker, multi-replica deployments diverge. Either drop the in-memory graph or push it into a shared store (Redis, Neo4j, or recompute from Postgres on demand).
2. **Single-slot NLP semaphore**. One spaCy/embedder/cross-encoder call at a time per process. With Render single-worker, this is a hard ceiling on memory write throughput.
3. **Cross-encoder re-rank loaded lazily on the request thread**. First request after deploy or after eviction blocks for tens of seconds.
4. **`asyncio.create_task(_background_rebuild)` at startup** scans **all users** and rebuilds graph state. As users grow, startup becomes O(n).
5. **Engrams have no `app_id`**. Multi-app customers cannot scope engrams; cross-app contamination is possible.
6. **Migration runs in startup command**. Can't safely scale to multiple replicas without a migration lock.
7. **Celery beat enqueues per-user consolidation every 30 min**. Linear in users; will saturate Redis and OpenAI.
8. **Sentry traces/profiles at 1.0 sample rate**. Cost will spike with traffic.
9. **Slowapi without Redis** is per-process. With more than one worker the global limit is misleading.
10. **No connection pooler in production path** (PgBouncer is in `docker-compose.yml` but `render.yaml` connects directly). Acceptable up to the Supabase pooler limit, but degrades fast under burst.
11. **No queue depth metrics, no Celery DLQ, no autoscaling triggers**.
12. **Database lacks FKs from memory tables to `users`**. As tenant churn grows, orphaned data accumulates without a sweeper.

---

## 14. Funding blockers

These will surface during a real technical due-diligence pass:

1. **Roadmap claims do not match code**. `PRODUCTION_READINESS_PLAN.md` marks all 6 phases complete; quotas, secrets management, observability dashboards, and load testing are not actually done. `PROJECT_STATUS.md` calls multi-tenancy, GDPR, rate limiting, and testing "✅ Complete." A reviewer will catch this in 30 minutes and lose trust.
2. **A live database password is checked into the repo.** Investors will treat this as a culture-of-security issue, not a fixable bug.
3. **No billing, no real signup flow, no paying customer.** The pricing page exists; the billing pipe does not. Any "ARR" or "paying customer" claim will not survive diligence.
4. **CI is theatre**: lint + demo-mode tests + bandit-on-HIGH-only. There is no gate that prevents shipping a regression on auth, RLS, or schema.
5. **Multi-tenant security story is partial**. RLS is on memory tables only; `apps` are scope strings on API keys, not entities; engrams have no scope. A SOC-2 / ISO assessment would not pass.
6. **No data-residency, no DPA, no audit log**. Disqualifies enterprise sales.
7. **"5:1 engram compression" and "0.8 ms recall latency" are claimed in the marketing site without benchmarks in the repo.** A reviewer will ask for the harness.
8. **Single-author cadence signals**: hand-paste SQL files alongside Alembic, multiple deploy scripts, README/PROJECT_OVERVIEW/PROJECT_STATUS partly contradicting each other. Suggests a single founder/engineer, which raises bus-factor concerns.
9. **Production stack on Render free tier**. Implies the team is funding-constrained and has not yet operated the system under real load.
10. **No security policy file** (`SECURITY.md`), no responsible disclosure path, no `LICENSE` discoverable at root. The codebase is an alpha SaaS with no documented terms.

---

## 15. Top 10 next actions

In priority order. Each one is a concrete, atomic change.

1. **Rotate the Supabase password** (and any Supabase service-role keys for project `***REDACTED_PROJECT_ID***`). Scrub `alembic/env.py`, `scripts/apply_migrations.py`, and the git history. Add a pre-commit hook for `gitleaks` or `trufflehog`.
2. **Make `DEMO_MODE=true` impossible in production**: hard-fail at startup if `ENVIRONMENT=production` and `DEMO_MODE` is truthy or `SECRET_KEY` matches the default.
3. **Guard migration 007**: detect current vector dim before `DELETE`. Add a `BACKUP_BEFORE_DESTRUCTIVE_MIGRATION=true` gate. Document the data loss in `ALEMBIC_NOTES.md`.
4. **Wire `check_quota` into all write routers** (`/episodes`, `/semantics`, `/episode/write`, `/rag/chat`). Without this, quotas are decorative.
5. **Replace the `apps` scope-string with a real `apps` table** with FK to `users`, FK from memory rows, and RLS. Engrams need an `app_id` column too.
6. **Real CI matrix**: spin up a Postgres+Redis service in GitHub Actions and run the gated tests (`RUN_DB_TESTS=1`, `RUN_ML_TESTS=1` after caching the spaCy and sentence-transformer downloads). Fail the build on bandit MEDIUM. Add `pip-audit` and `npm audit`.
7. **Fix the test suite drift**: update `test_memory.py` service-name assertion, fix `locustfile.py` to use real routes, and add an end-to-end RLS test that proves user A cannot read user B from a real Postgres instance.
8. **Stop hiding LLM outages with `_generate_demo_reply`**. Surface a `503` or a structured error so customers see real failures.
9. **Build the missing onboarding loop**: a minimal sign-up + API-key-issuance UI in the Next.js landing site (or a dedicated dashboard). Remove the Streamlit "paste a token" pattern from any customer-facing path.
10. **Reconcile docs with reality**. Mark every claim in `PROJECT_STATUS.md` and `PRODUCTION_READINESS_PLAN.md` against the code. Demote anything not actually shipped to "in progress."

---

## 16. Open questions

Items where I could not determine intent or correctness from code alone:

- **Why does `alembic/env.py` carry a hardcoded Supabase URL with a real password?** Was this intentional fail-safe, or accidental leak after debugging?
- **Are there any current paying customers / API keys in production?** If yes, the password rotation must be coordinated with customer outage windows.
- **Does the marketing site claim of "5:1 engram compression"** correspond to an actual benchmark? I found no harness in the repo.
- **Is there a separate staging environment** not represented in `render.yaml`? Diligence will ask.
- **What is the intended relationship between `app_id` and `apps`?** The code today uses scope strings; the `gdpr.py` consent endpoint scopes by `app_id IS NULL`. Pick one model and migrate.
- **Is the `mnemo` → `nexmem` rename complete?** `pyproject.toml` for the MCP exposes both `nexmem-mcp` and `mnemo-mcp` entry points. `build_page.py` still has `App.tsx` → `mnemo-landing` style transformations.
- **What happens when an API key issued under an `app_id` is revoked?** Memories created under that `app_id` are not deleted. Is that the intended retention policy?
- **Why is `frontend/.streamlit/secrets.toml` committed?** Even though it only contains a localhost URL, the file should not be in the repo.
- **Why does Sentry use `traces_sample_rate=1.0`?** This is a cost bomb at any non-trivial traffic.
- **Does the team intend to use Supabase long-term?** The current schema, migrations, and Render blueprint are all Supabase-coupled, but `psycopg2-binary` is in `requirements.txt` purely for Alembic offline mode. If a customer needs self-hosted Postgres, the path is partially blocked by the hardcoded fallback in `alembic/env.py`.
- **What is the data-deletion SLA?** The GDPR delete endpoint is synchronous and assumes no external sync (Sentry, embeddings warehouse, customer logs) holds copies of the data.
