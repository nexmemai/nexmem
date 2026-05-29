# Kiro Work Log

> **Verified against GitHub on 2026-05-23.** This log was rewritten after the founder pointed to 11 open Kiro PRs that were not visible to `github_list_pull_requests` from the original sandbox session. Every PR number, branch, commit SHA, and step ID below was checked by fetching each branch's head SHA and reading the actual commit log against `origin/main`. Items that could not be verified are flagged **unverified**. Nothing is overclaimed.

---

## 1. Project context

- **Project:** Nexmem — a persistent AI memory layer for LLM agents (FastAPI + Postgres/pgvector + Redis + Celery + spaCy + sentence-transformers + OpenAI). See `PROJECT_OVERVIEW.md` and `README.md`.
- **Effort tracked here:** the multi-phase backend hardening sprint that took the codebase from MVP toward private-beta-readiness — credentials, auth, RLS, transactions, quotas, observability, Celery hardening, abuse vectors, audit logs, and SOC2 prep.
- **Scope of this file:** Kiro's verifiable work only. The single non-Kiro commit on `main` (`56afcdb` by `Memory Layer Dev`) is recorded as background context.

---

## 2. High-level summary

| Item | Value | Source |
| --- | --- | --- |
| Phases worked on by Kiro | **9** (P1, P2, P3, P5, P6, P7, P9, P10, P11/P12) | commit messages across 11 PRs |
| PRs opened by Kiro | **11** open, **0** merged, **0** closed | PRs #1–#11 |
| This log's own PR | **1** open (PR #12) | `kiro/work-log` |
| Unique Kiro commits across all 11 PR heads | **55** | `git log --not origin/main` over the 11 head SHAs |
| Total step / risk IDs introduced in plans | C1–C12 (P1), P2-C1..C10 (P2-A), P2-S0..S10 (P2-B), P3-A1..A10, P4-B1..B7, P5-C1..C10, P6-D1..D10, P7-E1..E10, P8-F1..F8, P9-G1..G7, P10-H1..H7, P11-I*, P12-J* | `BACKEND_HARDENING_PHASE2.md`, `BACKEND_HARDENING_PHASE3_PLUS.md` |
| Default branch | `main` (one non-Kiro commit `56afcdb`) | `git log` |
| Canonical hardening stack | `main → PR#3 → PR#5 → PR#6 → PR#7 → PR#8 → PR#9 → PR#10 → PR#11` | `git log` per branch |
| Alternative Phase 2 lineage | PR#1 → PR#2 (uses `C*` and `P2-C*` IDs; **not** the basis for PR#5+) | `git log` per branch |

**Backend readiness summary (from the canonical stack at PR#11 head, vs `main`):**

- **Secrets:** hardcoded Supabase password removed from `alembic/env.py`; `render.yaml` switched to `sync: false`; CI secret-scan hook added (`scripts/scan_secrets.py`).
- **Production startup safety:** `validate_production` now **raises** (no longer just warns) on weak `SECRET_KEY`, missing `DATABASE_URL`, or wildcard CORS in non-demo mode.
- **Auth & sessions:** `refresh_tokens` table (migration 012), refresh-token rotation + replay rejection, `/auth/logout` and `/auth/logout-all`, demo-auth isolated to `app/core/demo_auth.py`, brute-force protection via Redis.
- **RLS:** migration 013 extends RLS from memory tables to `users`, `api_keys`, `refresh_tokens`, `token_usage`. Per-request RLS context lifecycle owned by middleware.
- **Transactional writes:** `/memory/episode/write` precomputes NLP outside the DB transaction, then commits all four memory inserts atomically.
- **Concurrency:** new `app/core/concurrency.py` with bounded executor pools for embedder / nlp / reranker; heavy callers refactored.
- **Quotas:** `app/core/quotas.py` with monthly write + read caps wired into episodic, semantic, procedural, graph, RAG, and `memory/episode/write`.
- **Observability:** structured JSON logs with `request_id`, `user_id`, `app_id`, route, status, latency; Sentry traces sampled at 0.1 with `before_send` PII scrubbing.
- **Migrations:** `scripts/run_with_migrations.sh` acquires a Postgres advisory lock to prevent multi-replica races; CI runs migrations against a real Postgres service.
- **Celery hardening:** real DLQ for failed consolidation, idempotency lock, NLP moved out of DB transaction, RLS context applied inside tasks, soft / hard time limits, OpenAI circuit breaker, DLQ inspection CLI.
- **Abuse vectors:** request body size cap, JSON depth/size DoS guards, streaming GDPR export, atomic GDPR delete.
- **Compliance:** `auth_audit_log` and `gdpr_audit_log` tables, atomic API-key rotation with grace period, `SECURITY.md`, API versioning doc, CodeQL CI job.
- **Read-only kill switch:** `READ_ONLY=true` env-var causes every write route to 503 (P9-G1).

**Important caveat:** none of the 11 PRs is merged. Everything above is shipped **on branches**, not on `main`. See Section 8 and Section 11.



---

## 3. Completed work

> All "completed" items below are shipped **on a Kiro branch, not on `main`**, because no Kiro PR has been merged yet. Merging is an operator action (Section 9). Step IDs match commit messages and the planning docs (`BACKEND_HARDENING_PHASE2.md`, `BACKEND_HARDENING_PHASE3_PLUS.md`, `BACKEND_RISKS.md`).

### 3.1 Phase 1 completed (PR #1)

PR #1 — *"Backend hardening for private beta: secrets, auth bypass, migrations, quotas, RLS, CI"* — branch `backend/hardening-private-beta`, 8 commits, head `d65c7b5`.

| ID | Title | Notable files | Commit | Status |
|---|---|---|---|---|
| (planning) | Repo audit + hardening plan + risk register | `REPO_STATE_AUDIT.md`, `BACKEND_HARDENING_PLAN.md`, `BACKEND_RISKS.md` | `39c750e` | complete |
| C1 | Remove all hardcoded Supabase credentials | `app/config.py`, `scripts/clear_keys.py`, `scripts/migrate_to_uuid.py`, `scripts/apply_migrations.py` | `7e3c415` | complete |
| C2/C3/C4 | Hard-fail on unsafe production config | `app/config.py`, `app/main.py`, `tests/test_config_safety.py`, `tests/test_app_startup_safety.py` | `84b972b` | complete |
| C5 | Make migration 007 non-destructive on already-correct schema | `alembic/versions/007_standardize_vector_dim.py`, `tests/test_migration_007_safety.py` | `41cc344` | complete |
| C6 | Wire per-user monthly write quota into write routers | `app/core/quota.py` (new), `app/routers/{episodic,semantic,memory,rag}.py`, `tests/test_quota.py`, `tests/test_quota_wired_into_routers.py` | `ad44648` | complete |
| C7/C8/C9 | Real Postgres + Redis integration tests | new `tests/integration/` tree, `tests/integration/conftest.py`, `test_auth_real_db.py`, `test_quota_real_redis.py`, `test_rls_isolation.py`, `test_migrations.py` | `851efc9` | complete |
| C10/C12 | Locust route fix + stale-assertion fix | `tests/locustfile.py`, `tests/test_locustfile_targets_real_routes.py` | `11d1d9a` | complete |
| C11 | Docs truth pass | `PROJECT_STATUS.md`, `PRODUCTION_READINESS_PLAN.md` | `d65c7b5` | complete |

**Note (verified honesty):** `BACKEND_HARDENING_PHASE2.md` later audited Phase 1 and recorded that several of these claims were *partial* on entry to Phase 2 — e.g. credentials were still in `alembic/env.py`, `validate_production` only logged warnings, and quota enforcement was still not actually wired. Phase 2 reconciled all of those. Counting Phase 1 alone, on its own branch, as "complete" is therefore a bit generous; the **canonical fix** for these issues is in PR #3 (Phase 2 lineage B).

### 3.2 Phase 2 completed — two parallel lineages

#### 3.2.A Phase 2 lineage A (PR #2)

PR #2 — *"Phase 2 backend hardening: atomicity, RLS expansion, refresh-token revocation, observability"* — branch `backend/hardening-phase2`, head `c812eea`. Built on top of PR #1; uses `P2-C*` IDs.

| ID | Title | Commit |
|---|---|---|
| P2-C1 | Secret scan + incident runbook (and runtime fixture follow-up `c812eea`) | `857a5c8`, `c812eea` |
| P2-C2 | Transactional memory writes (R-H1) | `8ef8f21` |
| P2-C3 | App scope consistency (R-H2, R-H5) | `1ebfb6e` |
| P2-C4 | Expand RLS to users / api_keys / token_usage (R-H7) | `e232044` |
| P2-C5 | Refresh-token revocation (R-H11) | `39e100f` |
| P2-C6 | Migration race + advisory lock (R-H10) | `741d138` |
| P2-C7 | Cold-start audit: pre-warm + first-load timing (R-M2/R-M3) | `7070275` |
| P2-C8 | Observability hardening (R-H9 / R-M10) | `ea09466` |
| P2-C9 | CI truthfulness + coverage signal | `75791dd` |
| P2-C10 | Truth pass on repo status | `2c59b96` |

**Status:** complete on branch. **This lineage is not what PRs #5–#11 build on** — see 3.2.B.

#### 3.2.B Phase 2 lineage B (PR #3, canonical)

PR #3 — *"Phase 2 backend hardening: secrets, sessions, RLS, transactional writes, quotas, observability"* — branch `chore/p2-backend-hardening`, 11 commits, head `4bdd8e5`. Started fresh from `main`; uses `P2-S*` IDs. **This is the lineage all later PRs build on.**

| ID | Title | Notable files | Commit |
|---|---|---|---|
| P2-S0 | Planning docs and risk register | `BACKEND_HARDENING_PHASE2.md`, `BACKEND_RISKS.md`, `docs/INCIDENT_RUNBOOK.md` | `199c1f8` |
| P2-S1 | Secret hygiene complete | fixed `alembic/env.py`, scrubbed scripts, `render.yaml` `sync: false`, `scripts/scan_secrets.py`, `scripts/run_with_migrations.sh` | `b547381` |
| P2-S2 | Auth and session safety | `app/models/auth.py::RefreshToken`, migration `012_refresh_tokens.py`, `app/core/demo_auth.py`, decode_token hardening, `/auth/logout`, `/auth/logout-all`, refresh rotation + replay rejection | `d013f62` |
| P2-S3 | RLS + app scope consistency | `docs/APP_SCOPING.md`, migration `013_extend_rls.py` (RLS on `api_keys` / `refresh_tokens` / `token_usage` / `users`), removed `current_user.app_id` AttributeError path | `4a2fa21` |
| P2-S4 | Transactional writes for `/memory/episode/write` | precompute NLP outside tx, atomic rollback on failure, `tests/test_transactional_writes.py`, integration test | `e562380` |
| P2-S5 | Bounded concurrency for async request paths | new `app/core/concurrency.py` (embedder / nlp / reranker pools), refactored heavy callers, Sentry sampling + scrubbing | `a89bae6` |
| P2-S6 | Deployment safety + contributor guidelines | `CONTRIBUTING.md`, Redis-aware `/health/ready`, advisory-lock wrapper | `1a78484` |
| P2-S7 | Structured logging with PII redaction | new `app/middleware/logging.py` with `request_id`/`user_id`/`app_id`/`latency_ms`, redaction tested in `tests/test_logging_redaction.py` | `b3ab0bf` |
| P2-S8 | Read/write quota enforcement (R-005, R-108) | `app/core/quotas.py`, write + read caps wired into all targeted routes, `tests/test_quotas.py` | `64a29ce` |
| P2-S9 | Test suite quality pass | pytest-cov in CI, unit-isolation sentinel test, marker discipline, `tests/test_unit_isolation.py` | `1854c54` |
| P2-S10 | Docs truth pass | rewrote `PROJECT_STATUS.md` + `PRODUCTION_READINESS_PLAN.md` against reality | `4bdd8e5` |

**Status:** complete on branch. The post-mortem at the bottom of `BACKEND_HARDENING_PHASE2.md` records this as the source-of-truth Phase 2.



### 3.3 Phase 3+ planning (PR #4)

PR #4 — *"Phase 3+ backend hardening plan: 10 future phases, ~70 IDs, priorities and acceptance criteria"* — branch `docs/backend-hardening-phase3-plan`, 1 commit `0e1aa65`.

- Adds `BACKEND_HARDENING_PHASE3_PLUS.md` — single source of truth for "what's left after Phase 2."
- Defines phases **P3 (auth & sessions)**, **P4 (apps as first-class)**, **P5 (DB/migration hardening)**, **P6 (Celery)**, **P7 (input safety / abuse)**, **P8 (observability deepening)**, **P9 (reliability / DR)**, **P10 (compliance / audit)**, **P11 (operator tooling)**, **P12 (API surface)** with stable IDs (e.g. `P3-A1`, `P5-C10`).
- Each ID has a priority (P0/P1/P2/P3) and an acceptance criterion.
- **Status:** docs-only, complete.

### 3.4 Phase 3 — Auth & sessions (PR #5)

PR #5 — *"Phase 3 backend hardening: email verification, password reset, sessions, register rate limit"* — branch `chore/p3-auth-hardening`, 4 net new commits on top of PR #3, head `cebc873`.

| ID | Title | Commit |
|---|---|---|
| P3-S0 | Schema for email verification + password reset | `41a8b96` |
| P3-S1 | Settings, schemas, helpers, demo store for Phase 3 auth | `a5803b9` |
| P3-S2 | Ship A1+A2+A3+A4+A8 endpoints with tests | `d20346a` |
| P3-S3 | Record Phase 3 hardening in `PROJECT_STATUS` | `cebc873` |

**Concretely shipped from the Phase 3 plan in PR #4:**
- **P3-A1** Email verification on registration
- **P3-A2** Password reset flow
- **P3-A3** Password change endpoint
- **P3-A4** Session listing endpoint (`GET /auth/sessions`, `DELETE /auth/sessions/{id}`)
- **P3-A8** Rate limit on `/auth/register`

Status: complete on branch.

### 3.5 P0 batch across phases 5/6/7/9 (PR #6)

PR #6 — *"P5/P6/P7/P9 P0 hardening: statement timeouts, Celery limits, body cap, streaming GDPR, read-only mode"* — branch `chore/p5-p6-p7-prod-hardening`, 4 net new commits on top of PR #5, head `861f8ba`.

| ID(s) | Title | Commit |
|---|---|---|
| P5-C1 + P6-D2 + P7-E5 | Per-conn statement timeout, Celery soft/hard time limits, request body cap | `4329fdd` |
| P7-E1 + P7-E2 | Stream GDPR export (NDJSON), atomic GDPR delete, demo parity | `ee8704b` |
| P9-G1 | Read-only kill switch (`READ_ONLY=true`) + JWT alg whitelist in middleware | `30b3c74` |
| (docs) | Record P5/P6/P7/P9 P0 pass in `PROJECT_STATUS.md` | `861f8ba` |

Status: complete on branch.

### 3.6 Phase 6 Celery hardening (PR #7)

PR #7 — *"Phase 6 Celery hardening: real DLQ, idempotency, NLP outside tx, RLS in tasks"* — branch `chore/p6-celery-hardening`, 1 net new commit on top of PR #6, head `77bd459`.

- **P6-D1** Real DLQ for failed consolidation (`consolidation_dlq` queue)
- **P6-D5** Idempotency lock (`SETNX` keyed by `consolidation:<user_id>:<window>`)
- **P6-D6** NLP / LLM moved outside the DB transaction in consolidation
- **P6-D9** RLS context applied inside Celery tasks (parity with HTTP path)

Status: complete on branch.

### 3.7 Before-public-beta batch (PR #8)

PR #8 — *"Before public beta: access-token blocklist, CI security jobs, SECURITY.md, API versioning"* — branch `chore/before-public-beta-batch`, 1 net new commit on top of PR #7, head `5ecb1f6`.

| ID | Title |
|---|---|
| P3-A5 | Access-token blocklist |
| P5-C5 | Forward + back migration test in CI |
| P10-H5 | `SECURITY.md` |
| P10-H6 | `pip-audit` (or equivalent) dependency CVE scan in CI |
| P12-J6 | API versioning (`docs/API_VERSIONING.md`) |

Status: complete on branch.

### 3.8 Audit logs + atomic key rotation (PR #9)

PR #9 — *"Before billing: audit logs (gdpr + auth), atomic API key rotation, test isolation"* — branch `chore/before-billing-audit-logs`, 1 net new commit on top of PR #8, head `06f89af`.

| ID | Title |
|---|---|
| P10-H1 | `gdpr_audit_log` table + writes from `/memory/user/{id}/export` and delete |
| P10-H2 | `auth_audit_log` table + writes from login, logout, password change, key creation, refresh-token revocation |
| P3-A10 | Atomic API key rotation endpoint with grace period |
| (test) | Test isolation fixes |

Status: complete on branch.

### 3.9 SOC2 batch 1 (PR #10)

PR #10 — *"Before SOC2 batch 1: OpenAI circuit breaker, JSON DoS guard, lockout escalation, DLQ CLI"* — branch `chore/before-soc2-polish`, 1 net new commit on top of PR #9, head `d3e000a`.

| ID | Title |
|---|---|
| P6-D7 | OpenAI circuit breaker (refuse new RAG/consolidation calls when global failure rate trips) |
| P7-E6 | JSON depth/size DoS guards |
| P3-A7 | Account lockout escalation (cross-IP failure aggregator) |
| P11-I5 | DLQ inspection / replay CLI |

Status: complete on branch.

### 3.10 SOC2 batch 2 (PR #11)

PR #11 — *"Before SOC2 batch 2: JSONB CHECK constraints, migration lint, Celery readiness probe, CodeQL"* — branch `chore/before-soc2-batch-2`, 1 net new commit on top of PR #10, head `9735b50`.

| ID | Title |
|---|---|
| P5-C9 | JSONB CHECK constraints on `episodic_memory.metadata`, `procedural_memory.settings`, etc. |
| P5-C6 | Migration safety lint (CI step) |
| P8-F7 | Celery queue depth + worker liveness in `/health/ready` |
| P10-H7 | CodeQL CI job |

Status: complete on branch.



---

## 4. In-progress work

| Title | Current state | What is left | Blocker | Status |
|---|---|---|---|---|
| **Merging the canonical hardening stack into `main`** | All 11 PRs are open; canonical stack is `#3 → #5 → #6 → #7 → #8 → #9 → #10 → #11` | Operator must review and merge in order; CI must be green for each | Operator-only — see Section 9 | in progress |
| **Reconciling the two Phase 2 lineages** | PR #2 (`P2-C*`) and PR #3 (`P2-S*`) are both open | Operator decides which to merge; if PR #3 is the canonical line (recommended — all later PRs build on it), PR #2 should be closed without merge | Operator decision | in progress |
| `KIRO_WORK_LOG.md` (this file) | Updated against verified PR/commit data | Push to `kiro/work-log` (PR #12) | None | in progress |

No other work is in flight in this Kiro session.

---

## 5. Remaining tasks

### 5.1 Critical remaining tasks (operator + Kiro)

These items block real private-beta traffic.

1. **Merge canonical hardening stack into `main`** in this order: PR #3, PR #5, PR #6, PR #7, PR #8, PR #9, PR #10, PR #11. Each PR's CI must be green. PR #4 (docs-only) can merge any time. PR #1 and PR #2 are superseded by PR #3 lineage and should be closed without merge after PR #3 lands (see `BACKEND_HARDENING_PHASE2.md` §1 and §8).
2. **Operator git-history rewrite** to purge the rotated Supabase password from commits prior to `b547381` (P2-S1). Procedure documented in `docs/INCIDENT_RUNBOOK.md` (introduced in PR #3). Required even though the credential is no longer in HEAD.
3. **Set `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `OPENAI_API_KEY`, `SENTRY_DSN`, and `ENVIRONMENT=production`** in Render. With PR #3 merged, `validate_production` will refuse to start without them.
4. **Confirm `DEMO_MODE` is unset (or `false`)** in Render production. With PR #3 merged, this is enforced at startup.
5. **Run `alembic upgrade head` against the live DB** through the new advisory-lock wrapper (`scripts/run_with_migrations.sh`). Heads after PR #3 include `012_refresh_tokens` and `013_extend_rls`.
6. **Confirm GitHub Actions CI is green on `main`** after each merge. The integration job runs `RUN_DB_TESTS=1` against a real Postgres + Redis service container (introduced in PR #1, hardened in PR #3 / PR #5 / PR #11).

### 5.2 High-priority engineering tasks (from `BACKEND_HARDENING_PHASE3_PLUS.md`)

Items still **planned but not shipped** in any open PR:

- **P3-A6** TOTP / 2FA (P2 priority, SOC2-track)
- **P3-A9** CAPTCHA / proof-of-work on signup (P3, deferred)
- **P4-B1..B7** Apps-as-first-class-model (whole phase) — `apps` table, FK on `api_keys.app_id`, registration quota, app-level RLS, app-level metrics, suspension, cross-app rules
- **P5-C2** Make DB pool sizing env-tunable (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`); document the math
- **P5-C3** Episodic memory partitioning by `created_at`
- **P5-C4** Archival policy for old episodic data
- **P5-C7** Replica lag check in `/health/ready` (no replica yet)
- **P5-C8** FK audit (`engrams → episodic_memory`, ON DELETE behaviour)
- **P5-C10** Backup restore drill (operator)
- **P6-D3** `worker_max_tasks_per_child=100`
- **P6-D4** `result_expires=3600s`
- **P6-D8** Backpressure when Celery queue depth high
- **P6-D10** Per-task structured logging in Celery
- **P7-E3** GDPR action audit log (the table is in PR #9; the action coverage may be partial — verify after merge)
- **P7-E4** Soft-delete grace period for GDPR delete
- **P7-E7** Per-route rate limits beyond auth
- **P7-E8** Per-authenticated-user rate limit (key by `user.id` if logged in)
- **P7-E9** Tighten error responses (no internal exception strings in `detail=`)
- **P7-E10** Document per-route response-size budget
- **P8-F1** Distributed tracing (OpenTelemetry)
- **P8-F2** Per-task structured logs (Celery) — partner item with P6-D10
- **P8-F3..F4** SLOs / error budgets and alerting rules
- **P8-F5** Cold-start latency Prometheus histogram
- **P8-F6** Embedder pool queue-depth metric
- **P8-F8** Log retention policy (operator + docs)
- **P9-G2** Graceful shutdown audit (uvicorn + Celery `task_acks_late=True`)
- **P9-G3..G6** Postgres / Redis / OpenAI outage runbooks + backup restore drill
- **P9-G7** Multi-region story (P3 — explicitly deferred)
- **P10-H3..H4** Data retention policy + DPA / SOC2 collateral
- **P11** Operator tooling (whole phase) — IDs other than `P11-I5` (which shipped in PR #10) are still open. Section is truncated in this workspace because `BACKEND_HARDENING_PHASE3_PLUS.md` continues past the visible 200-line slice.
- **P12** API surface phase — only `P12-J6` (versioning doc) shipped in PR #8.

### 5.3 Medium-priority product tasks

Out of scope for hardening, deferred:

- Billing / subscription tier wiring beyond per-tier quota knobs
- SDK polish: `nexmem-py`, `nexmem-js`, `nexmem-mcp`
- Reranker upgrade (cross-encoder vs Cohere/local)
- Streamlit dashboard interactive graph view
- Load-test verification with Locust against a production-shaped instance



---

## 6. Risks and known limitations

Sourced from `BACKEND_RISKS.md` (in PR #3) plus what was verified in this session.

| Risk | Severity | Impact | Status |
|---|---|---|---|
| **R-001** Supabase password leaked in `alembic/env.py` history | P0 | Full credential leak | **Resolved in working tree** by PR #3 (`b547381`). Operator must still rewrite history. |
| **R-002** `render.yaml` embedded Supabase pooler URL | P0 | Rotation requires code change | **Resolved** by PR #3 (`render.yaml` `sync: false`) |
| **R-003** `validate_production` did not enforce | P0 | Unsafe startup goes through | **Resolved** by PR #3 — now raises in non-demo mode |
| **R-004** `current_user.app_id` `AttributeError` | P0 | RAG path crashes | **Resolved** by PR #3 — body-scoped `app_id`, see `docs/APP_SCOPING.md` |
| **R-005** Quota enforcement was never wired | P0 | Free-tier abuse | **Resolved** by PR #3 — `app/core/quotas.py::enforce_write_quota` wired into all targeted writes |
| **R-006** Engine created at import time with hard validator | P0 | Test suite couldn't load | **Resolved** by PR #3 — demo-mode validator relaxation + sqlite fallback |
| **R-007** Migration race in multi-replica deploy | P0 | First deploy can corrupt schema | **Resolved** by PR #3 — `scripts/run_with_migrations.sh` uses Postgres advisory lock |
| **R-008** RLS context leak in `get_current_user` | P0 | Stale identity across requests | **Resolved** by PR #3 — middleware owns the contextvar lifecycle |
| **R-101** RLS only on memory tables | P1 | Service-role queries on `users`/`api_keys`/`token_usage` are unconstrained | **Resolved** by PR #3 (`013_extend_rls`) |
| **R-102** No real session revocation | P1 | Refresh tokens unrevocable before expiry | **Resolved** by PR #3 (`refresh_tokens` table) and PR #8 (access-token blocklist) |
| **R-103** Sentry config unsafe defaults (100% trace rate, no PII scrub) | P1 | Cost + leak risk | **Resolved** by PR #3 (P2-S5/S7 — `traces=0.1`, `before_send` PII scrub) |
| **R-104** `/health/ready` doesn't check Redis | P1 | False-positive readiness | **Resolved** by PR #3 (P2-S6) and extended in PR #11 (P8-F7) |
| **R-105** Multi-step writes are not transactional | P1 | Orphan rows on partial failure | **Resolved (production path)** by PR #3 (P2-S4) |
| **No Kiro PR is merged yet** | meta | Everything above is on branches; `main` still has the unsafe pre-Phase-2 code | **Open** — operator merge required (Section 9) |
| **Two Phase 2 lineages co-exist** (PR #2 `P2-C*` vs PR #3 `P2-S*`) | meta | Reviewer confusion + branch drift | **Open** — operator should pick PR #3 and close PR #2; `BACKEND_HARDENING_PHASE2.md` is the canonical record |
| **Integration tests still gated by `RUN_DB_TESTS=1`** | low | Default CI run does not exercise real DB | Accepted — separate CI job runs them; PR #3 hardens the layout |
| **External model downloads blocked locally** (spaCy, sentence-transformers) | low | ML tests skip in this sandbox | Accepted — `RUN_ML_TESTS=1` gate honored |
| **Multi-worker correctness for in-process state** (graph processor, APScheduler) | P2 | Inconsistent behaviour at scale | Partial — Celery now hardened (PR #7), but in-process graph state is still per-worker |

---

## 7. Test posture

| Metric | Value | Source |
|---|---|---|
| Test files on `main` (pre-Kiro) | 10 (1331 LOC) | `wc -l tests/*.py` |
| Test files at PR #11 head | substantially more — every Phase 2 step added at least one test (`test_app_scoping.py`, `test_concurrency.py`, `test_config_validate.py`, `test_logging_redaction.py`, `test_quotas.py`, `test_secret_scan.py`, `test_token_lifecycle.py`, `test_transactional_writes.py`, `test_transactional_writes_integration.py`, `test_unit_isolation.py`) | `git diff --name-status origin/main..9735b50c -- tests/` |
| Unit test count (exact) | **unverified locally** — `pytest --collect-only` failed in the sandbox because `httpx` was not installed in the default Python env | n/a |
| Integration tests | Present and gated by `RUN_DB_TESTS=1`; PR #1 reorganized them under `tests/integration/`; PR #3 added `test_transactional_writes_integration.py`; PR #11 added migration-lint tests | `tests/integration/` |
| ML tests | Gated by `RUN_ML_TESTS=1`; require spaCy + sentence-transformers | `tests/integration/test_engram_processor.py` |
| CI jobs on the canonical stack (after PR #11 merge) | `lint-and-test`, `security-audit` (bandit), `docker-build`, integration job (`RUN_DB_TESTS=1` against Postgres + Redis service), secret-scan (`scripts/scan_secrets.py`), forward+back migration test (P5-C5), migration-safety lint (P5-C6), CodeQL (P10-H7), `pip-audit` dependency CVE scan (P10-H6) | `.github/workflows/ci.yml` per branch |
| Coverage signal | `pytest-cov` added in PR #3 (P2-S9) | `BACKEND_HARDENING_PHASE2.md` §8.1 |

**Verified locally in this session:** none. **Verified in CI:** unverified — actual GitHub Actions run results were not retrieved (no `gh` CLI available, web fetch broken in this sandbox).



---

## 8. PRs, branches, and commits

All 11 hardening PRs are **open** and unmerged at the time of this log. Plus PR #12 for this log itself.

| PR | Branch | State | Title (abbreviated) | Head SHA | Net new commits | Builds on |
|---|---|---|---|---|---|---|
| **#1** | `backend/hardening-private-beta` | open | Backend hardening for private beta: secrets, auth bypass, migrations, quotas, RLS, CI | `d65c7b5` | 8 | `main` |
| **#2** | `backend/hardening-phase2` | open | Phase 2 backend hardening: atomicity, RLS expansion, refresh-token revocation, observability | `c812eea` | 11 (P2-C1..C10 + follow-up) | PR #1 |
| **#3** | `chore/p2-backend-hardening` | open | Phase 2 backend hardening: secrets, sessions, RLS, transactional writes, quotas, observability | `4bdd8e5` | 11 (P2-S0..S10) | `main` (canonical) |
| **#4** | `docs/backend-hardening-phase3-plan` | open | Phase 3+ backend hardening plan: 10 future phases, ~70 IDs | `0e1aa65` | 1 | `main` |
| **#5** | `chore/p3-auth-hardening` | open | Phase 3: email verification, password reset, sessions, register rate limit | `cebc873` | 4 (P3-S0..S3) | PR #3 |
| **#6** | `chore/p5-p6-p7-prod-hardening` | open | P5/P6/P7/P9 P0 hardening | `861f8ba` | 4 | PR #5 |
| **#7** | `chore/p6-celery-hardening` | open | Phase 6 Celery hardening | `77bd459` | 1 | PR #6 |
| **#8** | `chore/before-public-beta-batch` | open | Before public beta: blocklist, CI, security, versioning | `5ecb1f6` | 1 | PR #7 |
| **#9** | `chore/before-billing-audit-logs` | open | Before billing: audit logs, atomic API key rotation, test isolation | `06f89af` | 1 | PR #8 |
| **#10** | `chore/before-soc2-polish` | open | Before SOC2 batch 1: circuit breaker, JSON DoS, lockout, DLQ CLI | `d3e000a` | 1 | PR #9 |
| **#11** | `chore/before-soc2-batch-2` | open | Before SOC2 batch 2: JSONB CHECK, migration lint, Celery probe, CodeQL | `9735b50` | 1 | PR #10 |
| **#12** | `kiro/work-log` | open | docs: add `KIRO_WORK_LOG.md` (this file) | (head of branch) | 1 (initial) + this update | `main` |

Total **unique** Kiro commit SHAs across all 11 hardening PR heads: **55** (verified by `git log --not origin/main` over the union of head SHAs). Some of those 55 are content-equivalent rebases between PR #3 and PR #5 (e.g. `199c1f8` ≡ `0699092` for `P2-S0`); they count separately because the SHAs differ.

**One non-Kiro commit on `main`:** `56afcdb` by `Memory Layer Dev` — `chore: remove misconfigured vercel.json to let Vercel use Next.js defaults`.

---

## 9. Operator actions required

| # | Action | Why | Urgency | Where |
|---|---|---|---|---|
| 1 | **Merge PR #4** (Phase 3+ plan) | Adds the canonical `BACKEND_HARDENING_PHASE3_PLUS.md`; docs-only, low risk | High | GitHub PR #4 |
| 2 | **Decide between PR #2 and PR #3 for Phase 2.** Recommended: pick PR #3 (the basis for #5–#11) and close PR #2 without merge. | Two parallel implementations exist; merging both will conflict | **Critical** | GitHub PRs #2 / #3 |
| 3 | **Close PR #1 without merge after PR #3 merges.** PR #3 supersedes Phase 1 work and `BACKEND_HARDENING_PHASE2.md` records that several PR #1 claims were partial. | Avoid double-merging Phase 1 + Phase 2 | High | GitHub PR #1 |
| 4 | **Merge canonical stack in order**: PR #3 → PR #5 → PR #6 → PR #7 → PR #8 → PR #9 → PR #10 → PR #11. Each must be CI-green. | They are stacked; merging out of order will conflict | **Critical** | GitHub |
| 5 | **Operator git-history rewrite** to purge the rotated Supabase password from commits before `b547381`. Procedure in `docs/INCIDENT_RUNBOOK.md` (introduced in PR #3). Force-push afterwards. | Even after rotation, the leaked password is exfiltratable from history | **Critical** | Local clone with full depth + GitHub |
| 6 | **Set Render env vars** for the web service and Celery worker: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` (64-char), `OPENAI_API_KEY`, `SENTRY_DSN`, `ENVIRONMENT=production`. Ensure `DEMO_MODE` is **unset**. | After PR #3 merges, `validate_production` raises if any of these are unsafe | **Critical** | Render → Web Service → Environment |
| 7 | **Run `alembic upgrade head` against the live DB** through `scripts/run_with_migrations.sh`. Verify head matches `013_extend_rls` (after PR #3) | Schema drift between code and DB is the most common silent outage | **Critical** | Workstation with prod creds, or Supabase SQL editor |
| 8 | **Confirm GitHub Actions CI is green** on `main` after each merge (lint-and-test, integration, security-audit, docker-build, secret-scan, migration-lint, CodeQL, pip-audit). | CI green after merge proves the stack actually works | **High** | GitHub Actions |
| 9 | **Quarterly Supabase backup-restore drill** (P5-C10 / P9-G6). Time-box at 1 hour. Document in `docs/INCIDENT_RUNBOOK.md`. | Required before public beta and SOC2 | **High** | Supabase + workstation |
| 10 | **Merge PR #12** (`kiro/work-log`) so this file lives on `main` | Single source of truth for status | Medium | GitHub PR #12 |

---

## 10. Recommended next sequence

Dependency-aware. Do **not** start Phase 4 (apps-as-first-class) until the canonical stack is merged.

1. Merge **PR #4** (docs-only, low risk).
2. Close **PR #1** and **PR #2** without merge.
3. Operator: run secret scan over full history, rewrite history, force-push `main` (Section 9 #5).
4. Operator: set Render env vars and unset `DEMO_MODE` (Section 9 #6).
5. Merge **PR #3**, then run `alembic upgrade head` against the live DB through `scripts/run_with_migrations.sh`.
6. Merge **PR #5** through **PR #11** in order; verify CI is green between each merge.
7. Merge **PR #12** (this log).
8. Operator: confirm `/health/ready` returns 200 with all subsystems healthy on production.
9. Run the first backup-restore drill (P5-C10 / P9-G6).
10. Begin Phase 4 (apps-as-first-class, P4-B1..B7) and the remaining items in `BACKEND_HARDENING_PHASE3_PLUS.md`.

---

## 11. Go / no-go status

**GO WITH CONDITIONS** — for private beta — once the stack is merged and the operator actions in Section 9 (#5, #6, #7) are complete.

- **Why "with conditions" and not "GO":** the canonical hardening stack ships everything required for first real-user traffic, but it is **all on branches** today. `main` still has the pre-Phase-2 code that:
  - logs warnings instead of refusing on `DEMO_MODE=true` in production,
  - leaks `current_user.app_id` `AttributeError` in RAG,
  - never wires quota enforcement,
  - has no advisory lock around migrations,
  - has flat tests with no integration job.
- **Condition for "GO":**
  1. Canonical stack (PR #3 → #5 → #6 → #7 → #8 → #9 → #10 → #11) merged into `main` with CI green at each step.
  2. Operator git-history rewrite complete, force-pushed.
  3. Render env vars set; `DEMO_MODE` unset; live DB on `013_extend_rls`.
  4. `/health/ready` returns 200 in production.
  5. First backup-restore drill recorded in `docs/INCIDENT_RUNBOOK.md`.
- **Stays "NO-GO" if:** any of conditions 1–3 above is missing on the day of beta launch.

---

## 12. Changelog summary

(Chronological — Kiro work, oldest first. Dates are commit author dates from `git log`.)

- **Phase 1 (PR #1, branch `backend/hardening-private-beta`):** Opened with a repo audit (`REPO_STATE_AUDIT.md`), wrote `BACKEND_HARDENING_PLAN.md` and `BACKEND_RISKS.md`, removed hardcoded Supabase creds, added (warning-level) production config validation, fenced migration 007, wired a first cut of write quotas, restructured tests under `tests/integration/`, fixed Locust route targets, and reconciled status docs.
- **Phase 2 lineage A (PR #2, `backend/hardening-phase2`):** Built on PR #1; added P2-C1..C10 — secret scan, transactional writes, app-scope consistency, RLS expansion, refresh-token revocation, advisory-lock migrations, cold-start audit, observability hardening, CI truthfulness, and a second status truth pass.
- **Phase 2 lineage B (PR #3, `chore/p2-backend-hardening`, canonical):** Started fresh from `main`; shipped P2-S0..S10. The post-mortem in `BACKEND_HARDENING_PHASE2.md` records that several Phase 1 claims were *partial* on entry and were reconciled here. This is the lineage all later PRs build on.
- **Phase 3+ planning (PR #4, `docs/backend-hardening-phase3-plan`):** Authored `BACKEND_HARDENING_PHASE3_PLUS.md` — 10 future phases (P3..P12), ~70 stable IDs, priorities, acceptance criteria.
- **Phase 3 auth (PR #5, `chore/p3-auth-hardening`):** Shipped P3-S0..S3 and the P3-A1+A2+A3+A4+A8 endpoints — email verification, password reset, password change, session listing, `/auth/register` rate limit.
- **P0 batch (PR #6, `chore/p5-p6-p7-prod-hardening`):** Per-conn statement timeout (P5-C1), Celery time limits (P6-D2), request body cap (P7-E5), streaming GDPR export (P7-E1), atomic GDPR delete (P7-E2), read-only kill switch + JWT alg whitelist (P9-G1).
- **Celery hardening (PR #7, `chore/p6-celery-hardening`):** P6-D1 (real DLQ), P6-D5 (idempotency), P6-D6 (NLP outside tx), P6-D9 (RLS in tasks).
- **Public-beta batch (PR #8, `chore/before-public-beta-batch`):** P3-A5 (access-token blocklist), P5-C5 (forward+back migration test), P10-H5 (`SECURITY.md`), P10-H6 (dep CVE scan), P12-J6 (API versioning doc).
- **Audit logs + key rotation (PR #9, `chore/before-billing-audit-logs`):** P10-H1 (`gdpr_audit_log`), P10-H2 (`auth_audit_log`), P3-A10 (atomic API key rotation), test isolation fixes.
- **SOC2 batch 1 (PR #10, `chore/before-soc2-polish`):** P6-D7 (OpenAI circuit breaker), P7-E6 (JSON DoS guards), P3-A7 (lockout escalation), P11-I5 (DLQ CLI).
- **SOC2 batch 2 (PR #11, `chore/before-soc2-batch-2`):** P5-C9 (JSONB CHECKs), P5-C6 (migration safety lint), P8-F7 (Celery probe in `/health/ready`), P10-H7 (CodeQL).
- **2026-05-23 (this session, PR #12, `kiro/work-log`):** Added `KIRO_WORK_LOG.md`. Initial draft was conservative — PRs #1–#11 were not visible from `github_list_pull_requests` in the original sandbox session, so the file recorded "0 verified Kiro PRs" and asked the operator for the missing context. After the founder pointed to the 11 PRs by number and title, every PR was re-verified by fetching its head SHA via `github_get_branch_name_from_pull_request` and reading the actual commit log. This file was rewritten against that verified data.
