# BACKEND_RISKS.md

Living risk register for the NexMem backend. Each entry is verified against code; uncertain items are explicitly marked *unverified*.

Severity scale:
- **CRITICAL** — exploitable today or causes data loss / auth bypass / financial loss.
- **HIGH** — high probability of production incident; must be fixed before private beta with paying or recoverable customers.
- **MEDIUM** — important to fix before public launch; not strictly a beta blocker.

Each entry: ID · Severity · Title · Code pointer · Why it matters · Mitigation owner.

---

## Rotations Required (separate from code fixes)

These actions live outside the codebase and must be done by an operator. They are part of this hardening but are not "fix the code" tasks.

| Action | Why | Owner |
|---|---|---|
| Rotate Supabase database password for project `***REDACTED_PROJECT_ID***`. | The current password (`***REDACTED_PASSWORD***`) is committed in `alembic/env.py` and `scripts/apply_migrations.py`. Treat as compromised. | Founder / DBA |
| Scrub git history for the password (`git filter-repo` or BFG). | Even after removal, the password remains in history and is publicly accessible if the repo is ever made public. | Founder |
| Audit Supabase `service_role` key usage; rotate if any keys are in code or shared with third parties. | Service role bypasses RLS. *Unverified* whether any service-role keys are exposed. | Founder |
| Confirm no `.env.production` or `.env.local` is committed in any branch. | Both are gitignored, but an explicit `git log --all -- .env.local` is needed. *Unverified.* | Founder |
| Disable Supabase API access for any compromised IPs (post-rotation). | Defensive. | DBA |

---

## CRITICAL

### R-C1 · Hardcoded Supabase password in repository
- **Where:** `alembic/env.py:35-37`, `scripts/apply_migrations.py:7`, `scripts/clear_keys.py:5`, `scripts/migrate_to_uuid.py:5`.
- **What:** The Supabase production database password (`***REDACTED_PASSWORD***`, URL-encoded `***REDACTED_PASSWORD***`) is checked into the repo as a "fail-safe override" when the configured DATABASE_URL is missing or matches a stale hostname.
- **Why critical:** Anyone with read access to the repo, *current or historical*, has full DB access. Public repo or a leaked clone = total loss.
- **Mitigation:** Plan §C1. Hard-fail on missing DATABASE_URL; remove the literal.
- **Status:** ✅ FIXED on branch `backend/hardening-private-beta`. `alembic/env.py`, `scripts/apply_migrations.py`, `scripts/clear_keys.py`, `scripts/migrate_to_uuid.py` and `render.yaml` no longer contain any literal credentials. Verified by `tests/test_alembic_env.py::test_no_supabase_password_or_project_in_repo_source` which scans the whole repo. **The committed password must still be rotated in Supabase and scrubbed from git history — see §Rotations Required.**

### R-C2 · `DEMO_MODE=true` disables auth
- **Where:** `app/core/deps.py:18-22`.
- **What:** When `settings.demo_mode` is true, every authenticated route returns a synthetic `User(id=DEMO_USER_ID)`. The `Authorization` header is not even read.
- **Why critical:** A single mistaken env var in production means any unauthenticated client gets full access as the demo user, with all of that user's data accessible to all callers.
- **Mitigation:** Plan §C2. Hard-fail at startup when `environment=production` and `demo_mode=true`.

### R-C3 · Default `SECRET_KEY` accepted in production
- **Where:** `app/config.py:43`, `app/config.py:122-128`.
- **What:** Default `SECRET_KEY="local-dev-secret-change-this-before-production"` is in the published repo. `validate_production` was changed to a `WARNING` log instead of a `RuntimeError`. Comment in code: *"Removed the RuntimeError raise to prevent startup hangs."*
- **Why critical:** Anyone reading the repo can forge access tokens for any user against any deploy that forgot to override the secret. JWTs are HS256 — knowing the secret = full impersonation.
- **Mitigation:** Plan §C2 (combined). Re-introduce hard fail in production.

### R-C4 · CORS `*` with `allow_credentials=True`
- **Where:** `app/config.py:81`, `app/main.py:131-137`.
- **What:** Default `allowed_origins=["*"]` and `allow_credentials=True`. Browsers downgrade `*` when credentials are involved, but a non-browser client or a misconfigured deployment with a single bad origin entry is exploitable. Production env (`render.yaml`) sets specific origins, but the default in code is dangerous.
- **Why critical:** Cross-origin credentialed requests, CSRF on cookie-based clients (today there are none, but an MCP/desktop client could add one), and ambiguous browser behavior.
- **Mitigation:** Plan §C2/§C4. Either set `allow_credentials=False` when `*`, or hard-fail in production.

### R-C5 · Migration 007 destroys data unconditionally
- **Where:** `alembic/versions/007_standardize_vector_dim.py:18`.
- **What:** `op.execute("DELETE FROM semantic_memory")` runs on every `upgrade()`, with no guard. Re-running migrations on a populated DB silently destroys all semantic memory rows.
- **Why critical:** Deploys, restores, and DR drills all run `alembic upgrade head`. Any operator running migrations against a populated DB that is mid-chain (e.g., recovered from a backup at revision 006) will lose data.
- **Mitigation:** Plan §C5. Detect dim, skip when 384, gate destructive path behind `ALLOW_DESTRUCTIVE_MIGRATION=1`.

### R-C6 · Quotas not enforced
- **Where:** `app/core/rate_limit_redis.py:38-100` (`check_quota` defined). `grep -r 'check_quota' app/` returns only the definition. No router calls it.
- **What:** `User.tier` is set to `"free"` by default, monthly quotas are configured (`free_monthly_writes=1000`), but no router invokes `check_quota`. The only active limiter is the global IP-based slowapi (60/min).
- **Why critical:** A single free-tier customer can drive unbounded OpenAI cost via `/rag/chat` until the host runs out of money. There is no app/user-scoped throttle.
- **Mitigation:** Plan §C6. Wire as a FastAPI dependency on every write route. Fail closed on Redis unavailability when `REDIS_URL` is configured.

### R-C7 · Auth router is not demo-aware → "demo mode" tests do not actually run
- **Where:** `app/routers/auth.py:register/login`, `tests/conftest.py`, `pytest.ini`.
- **What:** Conftest forces `DEMO_MODE=true`. The `auth.register` endpoint never branches on demo mode — it tries to query and write to the real DB. Empirically: running `pytest` with `DEMO_MODE=true` and a fake `DATABASE_URL` produces 8 failures, 5 errors, 33 skipped, 13 passed (verified live by the reviewer).
- **Why critical:** The "zero-dependency test suite" claim in `PROJECT_STATUS.md` is false. CI today gives no real signal beyond import-time linting.
- **Mitigation:** Plan §C7. Either short-circuit auth.* in demo mode, or — preferred — replace demo-mode CI with a real Postgres service.

### R-C8 · CI test job has no security signal
- **Where:** `.github/workflows/ci.yml:30-40`.
- **What:** CI only runs `pytest` in demo mode against in-memory dicts. `RUN_DB_TESTS=1` and `RUN_ML_TESTS=1` are never set. Auth, RLS, migrations, and pgvector are not exercised.
- **Why critical:** Regressions to auth/RLS land silently. `bandit --severity-level high` is the only real security gate.
- **Mitigation:** Plan §C8/§C9. Add Postgres + Redis service containers, run gated tests.

---

## HIGH

### R-H1 · Unified `episode/write` is not transactional
- **Where:** `app/routers/memory.py:write_episode`.
- **What:** Episodic insert → semantic insert → engram process → engram insert → graph nodes → graph edges. Each block has its own try/except; on partial failure, the prior commits stand. There is no enclosing transaction or savepoint.
- **Risk:** Inconsistent state; "I wrote it but it's not in the engram." Hard to support.
- **Mitigation:** Wrap in a single async transaction; rollback on any failure.

### R-H2 · `current_user.app_id` AttributeError in `rag.py`
- **Where:** `app/routers/rag.py:153, 232`.
- **What:** `logger.info("llm_token_usage", app_id=current_user.app_id, ...)`. The `User` model has no `app_id` attribute. This crashes the request with `AttributeError` once `logger.info` runs in production mode.
- **Risk:** Every production `/rag/chat` call after the LLM responds returns a 500. *Unverified end-to-end* (might be silently swallowed by log-level filtering, but the code path exists).
- **Mitigation:** Replace with `getattr(current_user, "app_id", None)` or remove the field; track app id from request, not user.

### R-H3 · API-key auth bypasses the JWT-only RLS middleware
- **Where:** `app/main.py:200-216` (middleware only decodes Bearer JWT).
- **What:** `user_context_middleware` only sets `app.current_user_id` for `Bearer` tokens. API-key requests fall through. RLS GUC is later set by `set_rls_context` inside `get_current_user`, so behavior is correct in practice — but the dependency on call ordering is fragile.
- **Risk:** A future refactor that runs DB queries before `get_current_user` (e.g., a router-level dependency that opens a session early) will leak data because the GUC is unset, RLS predicate is `user_id = NULL`, which fails closed for reads but **also** for writes — surfaces as silent zero-rows.
- **Mitigation:** Decode API-key auth in the same middleware so the RLS GUC is set unconditionally. Add an integration test that runs a query in a router pre-dependency.

### R-H4 · `apps.register_app` accepts request body via query string
- **Where:** `app/routers/apps.py:24-31`.
- **What:** `app_name: str` and `description: Optional[str]` are unvalidated query params on a `POST`. Clients sending JSON bodies get 422 unless they also send the params in the URL.
- **Risk:** Breaks clients; reduces security review confidence.
- **Mitigation:** Add `RegisterAppRequest(BaseModel)` and accept in body.

### R-H5 · Engrams have no `app_id` and no FK to `users`
- **Where:** `app/models/engram.py`.
- **What:** `engrams` rows are scoped only by `user_id`; no `app_id`. RLS is enabled (m008) but multi-app scoping is leaky for engrams.
- **Risk:** A multi-app customer's engrams from one app are visible to queries scoped to another app.
- **Mitigation:** Add `app_id` column + index + RLS predicate update. Migration plus model change. (Defer to high-priority backlog because it requires a backfill plan.)

### R-H6 · No FKs from memory tables to `users`
- **Where:** `app/models/memory.py`. `user_id` is a UUID column with no `ForeignKey`.
- **What:** Deleting a user (admin path, RLS-bypassed) leaves orphaned rows.
- **Risk:** GDPR delete relies on hand-coded `DELETE` calls. Future admin tooling will leak orphan data.
- **Mitigation:** Add FKs in a migration with `ON DELETE CASCADE`.

### R-H7 · RLS missing on `users`, `api_keys`, `token_usage`
- **Where:** `alembic/versions/008_enable_memory_rls.py` covers memory tables only.
- **What:** A buggy code path that connects without `app.current_user_id` gets full read/write to all users, all keys, and all usage.
- **Risk:** Information disclosure; horizontal escalation if an SSRF or SQLi ever lands.
- **Mitigation:** Add RLS policies on these tables in a new migration; service-role usage must explicitly bypass.

### R-H8 · `_generate_demo_reply` masks LLM outages
- **Where:** `app/routers/rag.py:25-71`.
- **What:** Any LLM error returns a hardcoded string ("Hello! I'm your AI assistant…").
- **Risk:** A customer cannot distinguish a real outage from real content. Silent failure mode.
- **Mitigation:** Surface a structured 503; remove the fake reply.

### R-H9 · Sentry sample rates set to 1.0
- **Where:** `app/main.py:39-44`.
- **What:** `traces_sample_rate=1.0`, `profiles_sample_rate=1.0`. Cost-bomb under traffic.
- **Risk:** Bill shock + Sentry quota exhaustion drops error events.
- **Mitigation:** Lower to `0.1 / 0.0` in production.

### R-H10 · Migration runs on container start, no migration lock
- **Where:** `Dockerfile` / `render.yaml:11`.
- **What:** `alembic upgrade head && uvicorn …`. Multiple replicas race the migration runner.
- **Risk:** Concurrent migration crashes; partial schema state.
- **Mitigation:** Move migrations to a release/init job; use Postgres advisory lock if migrations must run in start path.

### R-H11 · No revocation for refresh tokens
- **Where:** `app/core/security.py`, `app/routers/auth.py:refresh_token`.
- **What:** Refresh tokens are JWTs. Compromised refresh token is valid until `exp` (7 days) unless `SECRET_KEY` rotates.
- **Risk:** Stolen refresh token = persistent foothold.
- **Mitigation:** Add a server-side denylist or kid-rotation. (Not blocker for private beta if we accept the risk and document it; reclassify to MEDIUM if we explicitly accept.)

### R-H12 · Locust file targets non-existent routes
- **Where:** `tests/locustfile.py:54-63`.
- **What:** `POST /api/v1/episodic/` is wrong; real route is `/api/v1/agents/{user_id}/episodes`. Same for `rag/chat` payload shape.
- **Risk:** Load tests show 422/404 noise; team is misled into believing the system "handled load."
- **Mitigation:** Plan §C10. Rewrite to real routes.

### R-H13 · Stale assertion in `test_memory.py`
- **Where:** `tests/test_memory.py:21-26`.
- **What:** `assert data["service"] == "Decentralized AI Memory Layer"`. Actual root returns `"NexMem - Decentralized AI Memory Layer"`. Even when run with a real DB, this test fails.
- **Risk:** False sense of test coverage; will mask real regressions when integration CI is enabled.
- **Mitigation:** Plan §C10. Update assertion.

---

## MEDIUM

### R-M1 · NetworkX graph is per-process
- **Where:** `app/services/engram_processor.py`.
- **What:** Process-local graph state. Multi-worker / multi-replica deployments diverge.
- **Risk:** Inconsistent graph context across replicas. Debugging hell.
- **Mitigation:** Re-derive on demand from `knowledge_edges` per request, or push to Redis / Neo4j.

### R-M2 · NLP semaphore is single-slot
- **Where:** `engram_processor.process_async` uses `Semaphore(1)`.
- **What:** Serializes spaCy + embedder + cross-encoder across the whole process.
- **Risk:** Throughput cliff; a single slow request blocks all others.
- **Mitigation:** Bound to CPU count; profile.

### R-M3 · Cold-start lazy model loading on the request thread
- **Where:** `engram_processor.LazyEngramProcessor.process_async`.
- **What:** First request loads ~230 MB of models on the request path.
- **Risk:** Multi-second p99 spike after every deploy / scale-up.
- **Mitigation:** Pre-warm in lifespan; pre-bake models into Docker image.

### R-M4 · Hand-paste SQL files drift from Alembic
- **Where:** `apply_migrations_supabase.sql`, `run_in_supabase_sql_editor.sql`, `verify_migrations.sql`, `supabase_migration_sql.sql`.
- **What:** Multiple manual SQL files alongside Alembic; `apply_migrations_supabase.sql` contains invalid SQL (`REFERENCES NULL`).
- **Risk:** A future operator runs the wrong file; silent schema drift.
- **Mitigation:** Move them to `archive/` or delete; canonicalize on Alembic.

### R-M5 · `/metrics` Bearer compare uses `==`
- **Where:** `app/main.py:267-272`.
- **What:** Plain string equality is timing-sensitive.
- **Risk:** Theoretical; `secrets.compare_digest` is one line away.
- **Mitigation:** Switch to `secrets.compare_digest`.

### R-M6 · API-key scopes are advisory only
- **Where:** `app/routers/auth.py:create_api_key` (`scopes="read,write"` hard-coded), no router checks scopes.
- **Risk:** Customers expect "read-only" keys; cannot have them.
- **Mitigation:** Add a `Depends(require_scope("write"))` and accept scopes on creation.

### R-M7 · `bandit` only fails on HIGH
- **Where:** `tests/run_security_audit.py`.
- **Risk:** Medium-severity findings (e.g., insecure defaults) accumulate silently.
- **Mitigation:** Fail on MEDIUM after a one-time triage of the existing findings.

### R-M8 · No dependency scanning
- **Where:** `requirements.txt`, `nexmem-js/package.json`.
- **Risk:** Vulnerable transitive dependencies.
- **Mitigation:** Add `pip-audit` and `npm audit`/`Dependabot`.

### R-M9 · No scheduled cleanup of expired episodes
- **Where:** `app/main.py:284-291` exposes `POST /memory/cleanup` but Celery beat only schedules consolidation, not cleanup.
- **Risk:** Unbounded `episodic_memory` growth.
- **Mitigation:** Add a beat task that calls `cleanup_expired_episodic_memory()`.

### R-M10 · Sentry / Prometheus / structlog not verified end-to-end
- **Where:** `app/main.py:39-44`, `_instrumentator = Instrumentator().instrument(app)`.
- **Risk:** Looks instrumented; might not surface anywhere. *Unverified.*
- **Mitigation:** Manual smoke after a deploy; out of scope for code-only PR.

### R-M11 · Health `/ready` does not check Redis
- **Where:** `app/routers/health.py`.
- **Risk:** Service reports "ready" while Celery is dead and rate-limit storage is broken.
- **Mitigation:** Add a `redis.ping()` to readiness when `REDIS_URL` is set.

### R-M12 · Signup endpoint not rate-limited beyond global IP limit
- **Where:** `app/routers/auth.py:register`.
- **Risk:** Mass-account creation, DB bloat.
- **Mitigation:** Per-IP and per-email throttle.

### R-M13 · `pytest.ini` does not set `DATABASE_URL`
- **Where:** `pytest.ini:7-10`.
- **Risk:** `pydantic` validator raises at import; tests un-runnable without an env shim. Plan §C7 fixes this by short-circuiting auth and adding a fake DATABASE_URL to pytest.ini.

### R-M14 · `event_loop` fixture deprecated
- **Where:** `tests/conftest.py:27`.
- **Risk:** pytest-asyncio future versions will break; minor.
- **Mitigation:** Switch to `event_loop_policy` fixture; not a blocker.

---

## Unverified items (need ops confirmation)

- Are there active customers / API keys against the current Supabase database? (If yes, password rotation must be coordinated.)
- Is there a non-public branch with secrets? (`git log --all -- .env*` would tell us.)
- Has Sentry actually received a sample event from the deployed service?
- Has `/metrics` been scraped by Prometheus successfully against a deploy?
- What is the current Render plan in production? (Code says `plan: free` everywhere; production might differ.)

---

## Tracking

Status updates land here as inline edits when items are mitigated. Format:

```
- [x] R-C1 — fixed in commit abcdef0; verified via test test_alembic_env::test_missing_db_url_fails_loud (CI run #123).
```
