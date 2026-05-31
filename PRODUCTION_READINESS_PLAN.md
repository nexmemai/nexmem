# Nexmem Production Readiness Plan

**Last reviewed:** 2026-05-22 (end of Phase 2 hardening).

This document is the operator-facing readiness checklist. It is
rewritten end-of-phase from the previous, overclaiming version. Each
item carries a verified status: *shipped*, *partial*, *not started*,
or *deferred*. Anything marked *partial* or worse is a real gap.

The previous "every box checked" version is replaced because it was
out of step with the working tree (Phase 1 had marked everything
complete while quota enforcement, structured logging redaction, and
secret hygiene were not actually wired). `BACKEND_HARDENING_PHASE2.md`
explains the reconciliation.

---

## Phase 1 — Auth, security, multi-tenancy

| Task | Status | Notes |
|------|--------|-------|
| Hashed API keys with constant-time lookup | shipped | `app/core/security.py::verify_api_key` uses `secrets.compare_digest`. |
| `Depends(get_current_user)` on protected routes | shipped | Audited in P2-S2. JWT `alg` whitelisted; expired tokens rejected; tests pin both. |
| Rate limiting + monthly quotas | shipped | `app/core/quotas.py` wired into all writes and the two read-heavy routes. Fails closed on Redis error. (Phase 1 had defined the function but never called it; Phase 2 wired it.) |
| Secrets management | partial | Render env vars used for production. Operator-side history rewrite still pending (R-201). CI scanner blocks new leaks. Move to a managed secrets provider is deferred. |
| Pydantic strict production validation | shipped | `validate_production` raises in non-demo mode if SECRET_KEY is weak/missing, DATABASE_URL is missing, or ALLOWED_ORIGINS is `*`. (Phase 1 only logged warnings.) |

## Phase 2 — Database scalability

| Task | Status | Notes |
|------|--------|-------|
| Connection pooling | shipped | Supabase pgbouncer transaction-mode pooler at port 6543; statement cache disabled. |
| HNSW indexes on vector columns | shipped | `002_hnsw_index.py`, `009_engram_hnsw_index.py`. |
| Idempotent migrations | partial | Phase 2 alembic env.py acquires an advisory lock so multi-replica deploys do not race. Migration 007 is destructive and is documented as a one-shot (R-205). Migration authoring rules added to `CONTRIBUTING.md`. |

## Phase 3 — Background processing

| Task | Status | Notes |
|------|--------|-------|
| Celery worker + beat | shipped | `render.yaml` defines both as separate services. |
| LLM resiliency (tenacity backoff) | shipped | `app/services/llm.py` and consolidation use exponential backoff with `retry_if_exception_type` for OpenAI transient failures. |
| Dead-letter queue for consolidation | partial | Celery's default acks_late + max_retries provides best-effort. A real DLQ topic is deferred. Failed consolidations are logged but not surfaced to operators yet. |

## Phase 4 — Observability

| Task | Status | Notes |
|------|--------|-------|
| Structured JSON logging | shipped | `structlog` configured at startup. HTTP middleware emits one log line per request with `request_id`, `user_id`, `app_id`, `route`, `method`, `status`, `latency_ms`, `client_ip`, `user_agent`. PII redaction is tested. |
| Prometheus metrics | shipped | `prometheus-fastapi-instrumentator` instruments every endpoint. `/metrics` is gated by `METRICS_SECRET_KEY` and returns 503 when unset. |
| Sentry integration | shipped | Initialized when `SENTRY_DSN` is set, with `traces_sample_rate=0.1`, `profiles_sample_rate=0`, and a `before_send` hook that strips credentialed headers + body fields. |
| LLM cost / token tracking | shipped | `track_token_usage` writes to `token_usage` (RLS-scoped) on every successful RAG call. |

## Phase 5 — CI/CD

| Task | Status | Notes |
|------|--------|-------|
| Multi-stage Docker build | shipped | `Dockerfile` is slim. |
| GitHub Actions pipeline | shipped | `secret-scan`, `lint-and-test` (with coverage), `integration-tests` (real Postgres + Redis service containers), `security-audit`, and `docker-build` jobs. |
| Render deployment via `render.yaml` | shipped | Migrations run via `scripts/run_with_migrations.sh` (advisory-locked). DATABASE_URL is now `sync: false` so the operator sets it in the dashboard rather than committing it. |
| Vercel frontend | shipped | Out of scope for backend hardening. |

## Phase 6 — Testing

| Task | Status | Notes |
|------|--------|-------|
| Unit + integration tests | shipped | 75 unit tests pass in CI. 1 integration test exercises rollback against real Postgres + Redis service containers. New tests added in P2 cover secret scanning, config validation, token lifecycle, app scoping, transactional writes, concurrency primitives, logging redaction, quota enforcement, and unit-isolation sentinel. |
| Load testing (Locust) | partial | `tests/locustfile.py` exists. Has not been run against production. R-204 is the live-load gap. |
| SAST (bandit) | shipped | `tests/run_security_audit.py` runs in the `security-audit` CI job. |

---

## Operator action still required before first user traffic

These cannot be performed by the agent; they require a human with
access to GitHub, Render, and Supabase:

1. **Rewrite git history** to purge the rotated Supabase password
   from older commits. Steps in `docs/INCIDENT_RUNBOOK.md`.
2. **Set DATABASE_URL** in the Render dashboard for both
   `nexmem-api` and `nexmem-celery-worker`. The values previously
   embedded in `render.yaml` were removed in P2-S1.
3. **Confirm REDIS_URL** is wired into the web service. The
   `nexmem-redis` service is provisioned in `render.yaml`.
4. **Apply migration 013_extend_rls** to the live database via the
   advisory-locked entrypoint. After this runs, the
   `users_login_lookup` policy must permit logins; verify with a
   test login.
5. **Generate a fresh `SECRET_KEY`** in the Render dashboard
   (`python -c "import secrets; print(secrets.token_hex(32))"`).
   This invalidates every issued JWT and forces re-login.

Once all five steps are done, the backend is ready for first real
user traffic. The Go/no-go recommendation is in
`BACKEND_HARDENING_PHASE2.md`.
