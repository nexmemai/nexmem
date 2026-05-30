# Nexmem Project Status

## 0. Block / Rewrite Status (2026-05-30)
- History rewrite: **DONE** — leaked Supabase password + GitHub PAT purged from all 22 branches + tags and force-pushed (see `HISTORY_REWRITE_COMPLETE.md`). No real secrets remain in remote history.
- Secret scanner: **fixed + passing** — SHA-256 hash-based tripwire replaced the broken post-rewrite regex (see `scripts/scan_secrets.py`).
- Block 10 (merge prep — docs + verification): **IN PROGRESS** on `chore/merge-prep`.
- Blocks 1–9: **DONE** on branches; not yet merged to `main`.

## 1. Confirmed Working (verified against source)
✅ Email/password registration and login with bcrypt-hashed passwords — app/routers/auth.py
✅ JWT access tokens with alg whitelist and expiry — app/core/security.py
✅ Refresh tokens stored hashed in the refresh_tokens table — app/models/memory.py
✅ API keys using nxm_ prefix — app/routers/auth.py
✅ Brute-force lockout on login — app/core/brute_force.py
✅ Email verification on registration — app/routers/auth.py
✅ Password reset flow — app/routers/auth.py
✅ Authenticated /auth/change-password — app/routers/auth.py
✅ Per-IP rate limit on /auth/register — app/routers/auth.py
✅ Five tables: episodic_memory, semantic_memory, procedural_memory, knowledge_nodes, knowledge_edges, plus engrams — app/models/memory.py
✅ Unified write endpoint with atomic transactions — app/routers/episodic.py
✅ Unified context assembly — app/routers/memory.py
✅ Hybrid RAG chat endpoint — app/routers/rag.py
✅ Quotas enforcement (read and write) — app/core/quotas.py
✅ Async safety concurrency pools — app/core/concurrency.py
✅ DB statement timeouts — app/database.py
✅ Celery time + memory bounds — app/config.py
✅ Request body cap — app/main.py
✅ GDPR routes hardened — app/routers/gdpr.py
✅ Read-only kill switch — app/main.py
✅ Advisory-lock wrapper for migrations — scripts/run_with_migrations.sh
✅ Structured logging — app/main.py
✅ Health check — app/routers/health.py
✅ Data retention policy — app/tasks.py
✅ App suspension — app/core/suspension_check.py
✅ App metrics — app/routers/apps.py
✅ nexmem-admin CLI — scripts/nexmem_admin.py
✅ Force logout — app/routers/admin.py
✅ Impersonation — app/routers/admin.py
✅ Usage analytics — app/routers/admin.py
✅ SDK quickstarts — examples/python_quickstart.py

## 2. Exists But Untested Against Live DB
⚠️ Alembic migrations 001-024 — valid SQL generated, live Postgres run requires operator

## 3. Requires Operator Action Before Working
🔧 OpenTelemetry tracing — code exists, OTEL_EXPORTER_OTLP_ENDPOINT must be set in Render
🔧 execute_scheduled_deletions — Celery Beat schedule not configured yet
🔧 enforce_data_retention — Celery Beat schedule not configured yet
🔧 Admin endpoints — inert without ADMIN_API_KEY set in Render

## 4. Known Limitations for Private Beta
- Redis fail-open: if Redis goes down, brute-force protection, slowapi rate limiter, and access token blocklist stop working (R-301, accepted for private beta).
- Narrow RLS coverage: RLS only on memory tables, api_keys, refresh_tokens, token_usage, and users.
- No real session revocation for access tokens: compromised tokens remain valid until 4-hour expiry (R-102).
- NetworkX graph is per-process: Requires running with `--workers 1` in production (R-107).
- Leaked Supabase password in git history: **RESOLVED** — git-history rewrite completed and force-pushed (R-201).
- No first-class apps model: App scoping is a column rather than a dedicated relational table (R-203).
- Destructive migration 007: Drops semantic memory table content, cannot be safely re-run without data loss (R-205).
- check_app_not_suspended fails open on DB error: Write proceeds even if suspension check fails due to database hiccup.
- TOTP session tokens have no dedicated rate limit: The complete-login route can be brute-forced.

## 5. Explicitly Deferred to Post-Launch
❌ Task P3-A9: CAPTCHA / proof-of-work on signup
❌ Task P9-G7: Multi-region deployment
❌ Task: Billing / Stripe / subscription productization
❌ Task: SDK published to PyPI / npm
❌ Task: MCP-server hardening beyond a smoke-tested skeleton
❌ Task: New connectors, webhooks, or third-party integrations
❌ Task: Cosmetic UI work
❌ Task: Reranker upgrade
❌ Task: Streamlit dashboard interactive graph view
❌ Task: Load-test verification with Locust against a production-shaped instance
