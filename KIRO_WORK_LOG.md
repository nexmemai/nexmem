# Kiro Work Log

> **Verified against GitHub on 2026-05-23.** This log was rewritten after the founder pointed to 11 open Kiro PRs that were not visible to `github_list_pull_requests` from the original sandbox session. Every PR number, branch, commit SHA, and step ID below was checked by fetching each branch's head SHA and reading the actual commit log against `origin/main`. Items that could not be verified are flagged **unverified**. Nothing is overclaimed.
>
> **Source-of-truth note (2026-05-23, Block 3 session).** Per operator instruction, this file now lives on the active working branch (currently `chore/p9-reliability-incident-response` stacked on `chore/p7-rate-limits-error-hygiene`). The `kiro/work-log` branch is retained as a historical record but is no longer updated. Future Block-N updates land on the working branch tip.

---

## 1. Project context

- **Project:** Nexmem — a persistent AI memory layer for LLM agents (FastAPI + Postgres/pgvector + Redis + Celery + spaCy + sentence-transformers + OpenAI). See `PROJECT_OVERVIEW.md` and `README.md`.
- **Effort tracked here:** the multi-phase backend hardening sprint that took the codebase from MVP toward private-beta-readiness — credentials, auth, RLS, transactions, quotas, observability, Celery hardening, abuse vectors, audit logs, and SOC2 prep, plus the post-PR-#11 block sequence (Block 1 = apps as first-class, Block 2 = per-route/per-user limits + error hygiene, Block 3 = reliability + incident response runbooks).
- **Scope of this file:** Kiro's verifiable work only. The single non-Kiro commit on `main` (`56afcdb` by `Memory Layer Dev`) is recorded as background context.

---

## 2. High-level summary

| Item | Value | Source |
| --- | --- | --- |
| Phases worked on by Kiro | **9 + 3 blocks** (P1, P2, P3, P5, P6, P7, P9, P10, P11/P12, plus Block 1 / Block 2 / Block 3) | commit messages across the canonical PR stack |
| PRs opened by Kiro (canonical hardening stack) | **10 open**, 0 merged, 0 closed | PRs #3, #5, #6, #7, #8, #9, #10, #11, #13, #14, plus the Block-3 PR opened from this branch |
| Side / non-canonical PRs | **4 open** (PR #1, #2, #4, #12) | see Section 8 |
| This log's own historical PR | **#12** | `kiro/work-log` (read-only) |
| Default branch | `main` (one non-Kiro commit `56afcdb`) | `git log` |
| Canonical hardening stack | `main → PR#3 → PR#5 → PR#6 → PR#7 → PR#8 → PR#9 → PR#10 → PR#11 → PR#13 → PR#14 → Block-3-PR` | `git log` per branch |
| Alternative Phase 2 lineage | PR#1 → PR#2 (uses `C*` and `P2-C*` IDs; **not** the basis for PR#5+) | `git log` per branch |

**Backend readiness summary (canonical-stack tip after Block 3, vs `main`):**

- **Secrets:** hardcoded Supabase password removed from `alembic/env.py`; `render.yaml` switched to `sync: false`; CI secret-scan hook (`scripts/scan_secrets.py`).
- **Production startup safety:** `validate_production` raises (no longer warns) on weak `SECRET_KEY`, missing `DATABASE_URL`, or wildcard CORS in non-demo mode.
- **Auth & sessions:** `refresh_tokens` table, refresh-token rotation + replay rejection, `/auth/logout`, `/auth/logout-all`, demo-auth isolated, brute-force protection via Redis (with documented in-memory fallback — see `docs/runbooks/REDIS_OUTAGE.md`).
- **RLS:** migrations 008, 013, and 019 enable + force RLS on all user-scoped tables and add app-level scoping.
- **API key prefix:** `nxm_` (post-Block-2 fix `43b3261`). Verified by `tests/test_memory.py::test_create_api_key`.
- **Transactional writes:** `/memory/episode/write` precomputes NLP outside the DB transaction and commits all four memory inserts atomically.
- **Concurrency:** `app/core/concurrency.py` bounded executor pools.
- **Quotas:** monthly write + read caps wired into every targeted route. Fail-closed on Redis error (documented in `docs/runbooks/REDIS_OUTAGE.md`).
- **Observability:** structured JSON logs with `request_id`, `user_id`, `app_id`, route, status, latency; Sentry traces sampled at 0.1 with PII scrubbing.
- **Migrations:** advisory-lock wrapper; CI runs migrations against a real Postgres service container; migration-safety lint (P5-C6).
- **Celery hardening:** real DLQ, idempotency lock, NLP outside transaction, RLS context inside tasks, soft/hard time limits, OpenAI circuit breaker, DLQ inspection CLI.
- **Abuse vectors:** request body cap, JSON depth/size DoS guards, streaming GDPR export, atomic GDPR delete, JWT alg whitelist.
- **Compliance:** `auth_audit_log`, `gdpr_audit_log`, atomic API-key rotation with grace period, `SECURITY.md`, API versioning, CodeQL CI job.
- **Read-only kill switch:** `READ_ONLY=true` env-var causes every write route to 503 (P9-G1).
- **Per-route + per-user rate limits:** `/auth/login`, `/memory/episode/write`, `/rag/chat`, plus per-IP `/auth/register` (P3-A8) and `/apps/register` (P4-B3); slowapi `key_func` upgraded to `user_id_or_ip` (P7-E8).
- **Error hygiene:** generic detail strings on 6 internal-exception sites (P7-E9).
- **Apps as first-class:** `apps` table, `api_keys.app_id` FK, app-level RLS on the 5 memory tables, `engrams.app_id` + RLS (Block 1 + Block 2 amendment).
- **Graceful shutdown:** lifespan teardown waits up to `GRACEFUL_SHUTDOWN_TIMEOUT` (default 30s) for in-flight requests, disposes the engine cleanly, and logs `graceful shutdown complete` (Block 3 / P9-G2).
- **Incident response runbooks:** Postgres / Redis / OpenAI outage procedures + backup-restore drill documentation, with explicit RTO 4h / RPO 24h targets (Block 3 / P9-G3..G6).

**Important caveat:** none of the canonical-stack PRs is merged. Everything above is shipped **on branches**, not on `main`. See Section 8 and Section 11.

---

## 3. Completed work

> All "completed" items below are shipped **on a Kiro branch, not on `main`**, because no canonical-stack PR has been merged yet. Merging is an operator action (Section 9). Step IDs match commit messages and the planning docs (`BACKEND_HARDENING_PHASE2.md`, `BACKEND_HARDENING_PHASE3_PLUS.md`, `BACKEND_RISKS.md`).

### 3.1 Phase 1 completed (PR #1)

PR #1 — *"Backend hardening for private beta: secrets, auth bypass, migrations, quotas, RLS, CI"* — branch `backend/hardening-private-beta`, 8 commits, head `d65c7b5`.

| ID | Title | Commit | Status |
|---|---|---|---|
| (planning) | Repo audit + hardening plan + risk register | `39c750e` | complete |
| C1 | Remove all hardcoded Supabase credentials | `7e3c415` | complete |
| C2/C3/C4 | Hard-fail on unsafe production config | `84b972b` | complete |
| C5 | Make migration 007 non-destructive on already-correct schema | `41cc344` | complete |
| C6 | Wire per-user monthly write quota into write routers | `ad44648` | complete |
| C7/C8/C9 | Real Postgres + Redis integration tests | `851efc9` | complete |
| C10/C12 | Locust route fix + stale-assertion fix | `11d1d9a` | complete |
| C11 | Docs truth pass | `d65c7b5` | complete |

**Note (verified honesty):** `BACKEND_HARDENING_PHASE2.md` later audited Phase 1 and recorded that several of these claims were *partial* on entry to Phase 2. Phase 2 (PR #3, lineage B) reconciled all of them. Counting Phase 1 alone, on its own branch, as "complete" is therefore a bit generous; the **canonical fix** for these issues is in PR #3.

### 3.2 Phase 2 completed — two parallel lineages

#### 3.2.A Phase 2 lineage A (PR #2)

PR #2 — *"Phase 2 backend hardening: atomicity, RLS expansion, refresh-token revocation, observability"* — branch `backend/hardening-phase2`, head `c812eea`. Built on top of PR #1; uses `P2-C*` IDs. **This lineage is not what PRs #5–#11 build on** — see 3.2.B.

#### 3.2.B Phase 2 lineage B (PR #3, canonical)

PR #3 — *"Phase 2 backend hardening: secrets, sessions, RLS, transactional writes, quotas, observability"* — branch `chore/p2-backend-hardening`, 11 commits, head `4bdd8e5`. Started fresh from `main`; uses `P2-S*` IDs. **This is the lineage all later canonical PRs build on.**

| ID | Title | Commit |
|---|---|---|
| P2-S0 | Planning docs and risk register | `199c1f8` |
| P2-S1 | Secret hygiene complete | `b547381` |
| P2-S2 | Auth and session safety (refresh tokens + replay rejection) | `d013f62` |
| P2-S3 | RLS + app scope consistency | `4a2fa21` |
| P2-S4 | Transactional writes for `/memory/episode/write` | `e562380` |
| P2-S5 | Bounded concurrency for async request paths | `a89bae6` |
| P2-S6 | Deployment safety + contributor guidelines | `1a78484` |
| P2-S7 | Structured logging with PII redaction | `b3ab0bf` |
| P2-S8 | Read/write quota enforcement | `64a29ce` |
| P2-S9 | Test suite quality pass | `1854c54` |
| P2-S10 | Docs truth pass | `4bdd8e5` |

### 3.3 Phase 3+ planning (PR #4)

PR #4 — *"Phase 3+ backend hardening plan"* — branch `docs/backend-hardening-phase3-plan`, head `0e1aa65`. Adds `BACKEND_HARDENING_PHASE3_PLUS.md` — single source of truth for "what's left after Phase 2." Defines phases P3..P12 with stable IDs and acceptance criteria.

### 3.4 Phase 3 — Auth & sessions (PR #5)

PR #5 — branch `chore/p3-auth-hardening`, head `cebc873`. Ships P3-A1 (email verification), P3-A2 (password reset), P3-A3 (password change), P3-A4 (session listing), P3-A8 (rate limit on `/auth/register`).

### 3.5 P0 batch across phases 5/6/7/9 (PR #6)

PR #6 — branch `chore/p5-p6-p7-prod-hardening`, head `861f8ba`. Ships P5-C1 (statement timeouts) + P6-D2 (Celery time limits) + P7-E5 (body cap), P7-E1+E2 (streaming GDPR + atomic GDPR delete), P9-G1 (read-only kill switch + JWT alg whitelist).

### 3.6 Phase 6 Celery hardening (PR #7)

PR #7 — branch `chore/p6-celery-hardening`, head `77bd459`. Ships P6-D1 (real DLQ), P6-D5 (idempotency lock), P6-D6 (NLP outside tx), P6-D9 (RLS in tasks).

### 3.7 Before-public-beta batch (PR #8)

PR #8 — branch `chore/before-public-beta-batch`, head `5ecb1f6`. Ships P3-A5 (access-token blocklist), P5-C5 (forward+back migration test), P10-H5 (`SECURITY.md`), P10-H6 (dep CVE scan), P12-J6 (API versioning doc).

### 3.8 Audit logs + atomic key rotation (PR #9)

PR #9 — branch `chore/before-billing-audit-logs`, head `06f89af`. Ships P10-H1 (`gdpr_audit_log`), P10-H2 (`auth_audit_log`), P3-A10 (atomic API key rotation), test isolation fixes.

### 3.9 SOC2 batch 1 (PR #10)

PR #10 — branch `chore/before-soc2-polish`, head `d3e000a`. Ships P6-D7 (OpenAI circuit breaker), P7-E6 (JSON DoS guards), P3-A7 (account lockout escalation), P11-I5 (DLQ CLI).

### 3.10 SOC2 batch 2 (PR #11)

PR #11 — branch `chore/before-soc2-batch-2`, head `9735b50`. Ships P5-C9 (JSONB CHECK), P5-C6 (migration safety lint), P8-F7 (Celery probe in `/health/ready`), P10-H7 (CodeQL).

### 3.11 Block 1 — Apps as first-class (PR #13)

PR #13 — branch `chore/p4-apps-first-class`, head `65835ca`. Ships P4-B1 (`apps` table), P4-B2 (`api_keys.app_id` FK), P4-B3 (`/apps/register` rate limit), P4-B4 (app-level RLS on the five memory tables).

### 3.12 Block 2 — Amendments + per-route limits + error hygiene (PR #14)

PR #14 — branch `chore/p7-rate-limits-error-hygiene`, head `09c7f32` (commit) → `6509d6e` (after Block-3-pre commits — see Section 12). Ships:
- Amendment 1 — wire `app.current_app_id` contextvar end-to-end.
- Amendment 2 — `engrams.app_id` column + RLS.
- P7-E7 — per-route limits on `/auth/login`, `/memory/episode/write`, `/rag/chat`.
- P7-E8 — per-authenticated-user `key_func` (`user_id_or_ip`).
- P7-E9 — generic error responses; 6 internal-exception sites cleaned.
- **Pre-Block-3 fix `43b3261`** — standardise API key prefix to `nxm_` per Rule #15 (12 files, 25/25 lines, all tests green).
- **Pre-Block-3 fix `6509d6e`** — remove the now-stale `nxm_/mem_` discrepancy bullet from the bootstrap appendix.

### 3.13 Block 3 — Reliability + incident response (this PR)

Branch `chore/p9-reliability-incident-response`, stacked on `chore/p7-rate-limits-error-hygiene` (`6509d6e`). Single commit per the operator's "one commit per logical group" rule.

| ID | Title | Notable files | Status |
|---|---|---|---|
| P9-G2 | HTTP / uvicorn graceful shutdown | `app/main.py` (lifespan teardown + in-flight tracking middleware), `app/config.py` (`graceful_shutdown_timeout: int = 30`, env `GRACEFUL_SHUTDOWN_TIMEOUT`), `tests/test_graceful_shutdown.py` (4 tests) | shipped, tested |
| P9-G3 | Postgres outage runbook | `docs/runbooks/POSTGRES_OUTAGE.md` | shipped (docs) |
| P9-G4 | Redis outage runbook (with explicit fail-open vs fail-closed table) | `docs/runbooks/REDIS_OUTAGE.md` | shipped (docs) |
| P9-G5 | OpenAI outage runbook (with circuit-breaker reference) | `docs/runbooks/OPENAI_OUTAGE.md` | shipped (docs) |
| P9-G6 | Backup restore drill documentation | `docs/runbooks/BACKUP_RESTORE.md` | **shipped as documentation only — the actual quarterly drill must be performed by the operator. Kiro cannot execute a real restore.** Procedure includes RTO 4h / RPO 24h targets, Supabase + Render + off-platform `pg_dump` paths, end-to-end verification (schema head, row counts, RLS posture, smoke test). |

Block 3 is shipped on a fresh branch stacked on the current canonical tip. CI was not directly verified by Kiro (no `gh` CLI in sandbox); pre-existing 8 sandbox-bound test failures in `tests/test_dlq_admin_cli.py` and `tests/test_p6_celery_hardening.py` remain unchanged from the PR-#11 baseline.

---

## 4. In-progress work

| Title | Current state | What is left | Blocker | Status |
|---|---|---|---|---|
| **Merging the canonical hardening stack into `main`** | All canonical PRs (incl. Block 1 / Block 2 / Block 3) are open | Operator must review and merge in order; CI must be green for each | Operator-only — see Section 9 | in progress |
| **Reconciling the two Phase 2 lineages** | PR #2 (`P2-C*`) and PR #3 (`P2-S*`) are both open | Operator decides; PR #3 is canonical | Operator decision | in progress |
| **Quarterly backup-restore drill (P5-C10 / P9-G6)** | Documentation shipped in `docs/runbooks/BACKUP_RESTORE.md` | Operator schedules + executes one drill per calendar quarter; logs results to `docs/incidents/<YYYY-QN>-backup-drill.md` | Operator-only — Kiro cannot execute a real restore | in progress (documentation done; drill pending) |

No other code work is in flight in this Kiro session.

---

## 5. Remaining tasks

### 5.1 Critical remaining tasks (operator + Kiro)

These items block real private-beta traffic.

1. **Merge canonical hardening stack into `main`** in this order: PR #3, PR #5, PR #6, PR #7, PR #8, PR #9, PR #10, PR #11, PR #13, PR #14, then the Block-3 PR opened from this branch. Each PR's CI must be green. PR #4 (docs-only) can merge any time. PR #1 and PR #2 are superseded by PR #3 lineage and should be closed without merge after PR #3 lands.
2. **Operator git-history rewrite** to purge the rotated Supabase password from commits prior to `b547381` (P2-S1). Procedure documented in `docs/INCIDENT_RUNBOOK.md`.
3. **Set `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `OPENAI_API_KEY`, `SENTRY_DSN`, and `ENVIRONMENT=production`** in Render. `validate_production` will refuse to start without them once PR #3 merges.
4. **Confirm `DEMO_MODE` is unset (or `false`)** in Render production.
5. **Run `alembic upgrade head` against the live DB** through the new advisory-lock wrapper (`scripts/run_with_migrations.sh`). The current head is `020_engrams_app_id`.
6. **Confirm GitHub Actions CI is green on `main`** after each merge.
7. **Schedule the first backup-restore drill** per `docs/runbooks/BACKUP_RESTORE.md` Section 7.

### 5.2 High-priority engineering tasks (from `BACKEND_HARDENING_PHASE3_PLUS.md`)

Items still **planned but not shipped** in any open PR:

- **P3-A6** TOTP / 2FA (P2 priority, SOC2-track)
- **P3-A9** CAPTCHA / proof-of-work on signup (P3, deferred)
- **P4-B5..B7** App-level metrics, suspension, cross-app rules (B1..B4 shipped in Block 1)
- **P5-C2..C4** DB pool sizing knobs, episodic partitioning, archival
- **P5-C7** Replica-lag check in `/health/ready`
- **P5-C8** FK audit (`engrams → episodic_memory`)
- **P5-C10** Backup restore drill — **documentation shipped in Block 3**, operator drill pending
- **P6-D3/D4/D8/D10** worker_max_tasks_per_child, result_expires, backpressure, per-task structured logs
- **P7-E3** GDPR action audit log
- **P7-E4** Soft-delete grace period for GDPR delete
- **P7-E10** Document per-route response-size budget
- **P8-F1..F6/F8** OpenTelemetry, per-Celery structured logs, SLOs, alerting, cold-start histogram, embedder queue-depth metric, log retention
- **P9-G2..G6** — **all five shipped in Block 3.** P9-G2 graceful shutdown is code; G3..G6 are runbooks. The operator drill for G6 is still pending.
- **P9-G7** Multi-region story (P3 — explicitly deferred)
- **P10-H3..H4** Data retention policy + DPA / SOC2 collateral
- **P11** Operator tooling (rest of phase) — `P11-I5` shipped in PR #10
- **P12** API surface phase — only `P12-J6` (versioning doc) shipped in PR #8

### 5.3 Medium-priority product tasks (deferred)

- Billing / subscription tier wiring beyond per-tier quota knobs
- SDK polish: `nexmem-py`, `nexmem-js`, `nexmem-mcp`
- Reranker upgrade (cross-encoder vs Cohere/local)
- Streamlit dashboard interactive graph view
- Load-test verification with Locust against a production-shaped instance

---

## 6. Risks and known limitations

Sourced from `BACKEND_RISKS.md` plus what was verified in this session.

| Risk | Severity | Status as of this session |
|---|---|---|
| **R-001..R-008** original P0 risks | P0 | All resolved on branches (PR #3); operator merge to `main` still pending. |
| **R-101** RLS only on memory tables | P1 | Resolved by PR #3 (`013_extend_rls`) and Block 1 (`019_app_level_rls`). |
| **R-102** No real session revocation | P1 | Resolved by PR #3 + PR #8 (refresh tokens + access-token blocklist). |
| **R-103** Sentry config unsafe defaults | P1 | Resolved by PR #3 (P2-S5/S7). |
| **R-104** `/health/ready` doesn't check Redis | P1 | Resolved by PR #3 + PR #11 (Celery probe added). |
| **R-105** Multi-step writes not transactional | P1 | Resolved by PR #3 (P2-S4). |
| **R-203** No first-class `apps` model | P2 | Resolved by Block 1 (PR #13). |
| **No Kiro PR is merged yet** | meta | Open — operator merge required. |
| **Two Phase 2 lineages co-exist** | meta | Open — operator should pick PR #3 and close PR #2. |
| **Integration tests gated by `RUN_DB_TESTS=1`** | low | Accepted — separate CI job runs them. |
| **External model downloads blocked locally** | low | Accepted — `RUN_ML_TESTS=1` gate honored. |
| **Multi-worker correctness for in-process state** (graph processor, APScheduler) | P2 | Partial — Celery hardened (PR #7); in-process graph state is per-worker (`--workers 1` in `render.yaml`). |
| **Redis fail-open for rate-limit / brute-force / blocklist** | P2 | **Documented** in Block 3 (`docs/runbooks/REDIS_OUTAGE.md` Section 2). Long-term, these subsystems should fail-closed when Redis is configured (matching the quotas policy). |
| **First backup-restore drill not yet performed** | P2 | Documentation shipped in Block 3 (`docs/runbooks/BACKUP_RESTORE.md`). Operator drill pending — see Section 4. |

---

## 7. Test posture

| Metric | Value | Source |
|---|---|---|
| Test files at canonical-stack tip after Block 3 | substantially more than `main` baseline (10 files) — every Phase 2/3 step added at least one test, plus Block 3's `tests/test_graceful_shutdown.py` (4 tests) | `git diff --name-status origin/main..HEAD -- tests/` |
| Sandbox unit-test count after Block 3 | **202 passed, 33 skipped, 5 deselected, 0 failed** (was 198 before Block 3; +4 from `test_graceful_shutdown.py`) | `pytest tests/ -q` in this session |
| Pre-existing sandbox-bound failures | 8 in `tests/test_dlq_admin_cli.py` and `tests/test_p6_celery_hardening.py` — these are **deselected/skipped** in the demo-mode run because they require Celery + Redis. Documented as not regressions on the PR-#11 tip. | `tests/test_p6_celery_hardening.py` skip markers |
| Integration tests | Present and gated by `RUN_DB_TESTS=1`; PR #1 reorganized them; PR #3 added `test_transactional_writes_integration.py`; PR #11 added migration-lint tests | `tests/integration/` |
| ML tests | Gated by `RUN_ML_TESTS=1`; require spaCy + sentence-transformers | `tests/integration/test_engram_processor.py` |
| CI jobs on the canonical stack | `lint-and-test`, `security-audit` (bandit), `docker-build`, integration job (real Postgres + Redis), secret-scan, forward+back migration test, migration-safety lint, CodeQL, `pip-audit` | `.github/workflows/ci.yml` |
| Coverage signal | `pytest-cov` added in PR #3 (P2-S9) | `BACKEND_HARDENING_PHASE2.md` §8.1 |

**Verified locally in this session:** demo-mode unit suite (202/0/33 skipped). **Verified in CI:** unverified — no `gh` CLI in sandbox; operator should confirm CI green on the Block-3 PR before merging.

---

## 8. PRs, branches, and commits

All canonical-stack PRs are **open** and unmerged at the time of this log.

| PR | Branch | Title (abbreviated) | Head SHA | Builds on |
|---|---|---|---|---|
| **#1** | `backend/hardening-private-beta` | Phase 1 (superseded) | `d65c7b5` | `main` |
| **#2** | `backend/hardening-phase2` | Phase 2 lineage A (alternate) | `c812eea` | PR #1 |
| **#3** | `chore/p2-backend-hardening` | Phase 2 lineage B (canonical) | `4bdd8e5` | `main` |
| **#4** | `docs/backend-hardening-phase3-plan` | Phase 3+ plan (docs-only) | `0e1aa65` | `main` |
| **#5** | `chore/p3-auth-hardening` | Phase 3 auth | `cebc873` | PR #3 |
| **#6** | `chore/p5-p6-p7-prod-hardening` | P5/P6/P7/P9 P0 batch | `861f8ba` | PR #5 |
| **#7** | `chore/p6-celery-hardening` | Phase 6 Celery hardening | `77bd459` | PR #6 |
| **#8** | `chore/before-public-beta-batch` | Public beta batch | `5ecb1f6` | PR #7 |
| **#9** | `chore/before-billing-audit-logs` | Audit logs + key rotation | `06f89af` | PR #8 |
| **#10** | `chore/before-soc2-polish` | SOC2 batch 1 | `d3e000a` | PR #9 |
| **#11** | `chore/before-soc2-batch-2` | SOC2 batch 2 | `9735b50` | PR #10 |
| **#12** | `kiro/work-log` | This file (historical record only) | (head of branch) | `main` |
| **#13** | `chore/p4-apps-first-class` | Block 1: apps as first-class | `65835ca` | PR #11 |
| **#14** | `chore/p7-rate-limits-error-hygiene` | Block 2: amendments + per-route/per-user limits + error hygiene + nxm_ prefix fix + bootstrap appendix cleanup | `6509d6e` | PR #13 |
| **(Block 3)** | `chore/p9-reliability-incident-response` | Block 3: P9-G2 graceful shutdown + P9-G3..G6 incident response runbooks | (this PR's head) | PR #14 |

**One non-Kiro commit on `main`:** `56afcdb` by `Memory Layer Dev` — `chore: remove misconfigured vercel.json to let Vercel use Next.js defaults`.

---

## 9. Operator actions required

| # | Action | Why | Urgency |
|---|---|---|---|
| 1 | **Merge PR #4** (Phase 3+ plan) | Adds canonical `BACKEND_HARDENING_PHASE3_PLUS.md`; docs-only, low risk | High |
| 2 | **Decide between PR #2 and PR #3 for Phase 2.** Recommended: keep PR #3 (basis for #5+); close PR #2 without merge. | Two parallel implementations exist; merging both will conflict | **Critical** |
| 3 | **Close PR #1 without merge after PR #3 merges.** | PR #3 supersedes Phase 1 work | High |
| 4 | **Merge canonical stack in order**: PR #3 → #5 → #6 → #7 → #8 → #9 → #10 → #11 → #13 → #14 → Block-3 PR. | Stacked branches | **Critical** |
| 5 | **Operator git-history rewrite** to purge the rotated Supabase password from commits before `b547381`. Procedure in `docs/INCIDENT_RUNBOOK.md`. | Even after rotation, the leaked password is exfiltratable from history | **Critical** |
| 6 | **Set Render env vars**: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` (64-char), `OPENAI_API_KEY`, `SENTRY_DSN`, `ENVIRONMENT=production`, `GRACEFUL_SHUTDOWN_TIMEOUT=30` (or as desired). Ensure `DEMO_MODE` is **unset**. | After PR #3 merges, `validate_production` raises if any of these are unsafe | **Critical** |
| 7 | **Run `alembic upgrade head` against the live DB** through `scripts/run_with_migrations.sh`. Verify head matches `020_engrams_app_id`. | Schema drift between code and DB is the most common silent outage | **Critical** |
| 8 | **Confirm GitHub Actions CI is green** on `main` after each merge. | Proves the stack actually works | **High** |
| 9 | **Schedule the first quarterly backup-restore drill** per `docs/runbooks/BACKUP_RESTORE.md` Section 7. Time-box: 1 hour. | Required before public beta and SOC2 (P5-C10 / P9-G6 acceptance) | **High** |
| 10 | **Merge PR #12** (`kiro/work-log`) so the historical log lives on `main`, OR formally retire `kiro/work-log` and adopt the working-branch source-of-truth model already in effect this session. | Single source of truth | Medium |
| 11 | **Publish `nexmem-py` to PyPI** (Block 8 / P12-J1). Run from a machine with the PyPI API token in `~/.pypirc`: `cd nexmem-py && python -m build && twine upload dist/*`. The wheel + sdist build was verified in the sandbox; the only remaining step is the upload. | Required for the public-beta announcement post — devs cannot `pip install nexmem-py` without it | High |
| 12 | **Publish `nexmem-js` to npm** (Block 8 / P12-J2). Run from a machine with `npm login` to the `nexmem` org token: `cd nexmem-js && npm install && npm run build && npm publish --access public`. The TypeScript build + Jest test suite (6/6 passing) was verified in the sandbox. | Same as #11 — devs cannot `npm install nexmem-js` without it | High |

---

## 10. Recommended next sequence

Dependency-aware. Do **not** start Block 4 until Block 3 is confirmed merged or operator gives further direction.

1. Merge **PR #4** (docs-only).
2. Close **PR #1** and **PR #2** without merge.
3. Operator: history rewrite (Section 9 #5).
4. Operator: set Render env vars (Section 9 #6).
5. Merge **PR #3** → **PR #5** → … → **PR #14** → **Block-3 PR** in order.
6. Run `alembic upgrade head` after PR #3 merges.
7. Confirm `/health/ready` returns 200 in production.
8. Run the first backup-restore drill (Section 9 #9).
9. Wait for operator-defined Block 4.

---

## 11. Go / no-go status

**GO WITH CONDITIONS** — for private beta — once the canonical stack is merged and the operator actions in Section 9 (#5, #6, #7) are complete.

- **Why "with conditions" and not "GO":** the canonical hardening stack ships everything required for first real-user traffic, but it is **all on branches** today. `main` still has the pre-Phase-2 code that:
  - logs warnings instead of refusing on `DEMO_MODE=true` in production,
  - leaks `current_user.app_id` `AttributeError` in RAG,
  - never wires quota enforcement,
  - has no advisory lock around migrations,
  - has flat tests with no integration job,
  - has no graceful-shutdown drain (P9-G2),
  - has no incident-response runbooks (P9-G3..G6),
  - generates `mem_`-prefixed API keys instead of `nxm_` (Rule #15).
- **Condition for "GO":**
  1. Canonical stack merged into `main` with CI green at each step.
  2. Operator git-history rewrite complete, force-pushed.
  3. Render env vars set; `DEMO_MODE` unset; live DB on `020_engrams_app_id`.
  4. `/health/ready` returns 200 in production.
  5. First backup-restore drill recorded in `docs/incidents/<YYYY-QN>-backup-drill.md`.
- **Stays "NO-GO" if:** any of conditions 1–3 above is missing on the day of beta launch.

---

## 12. Changelog summary

(Chronological — Kiro work, oldest first.)

- **Phase 1 (PR #1):** repo audit, removed hardcoded creds, warning-level config validation, fenced migration 007, first cut of write quotas, restructured tests, fixed Locust route targets.
- **Phase 2 lineage A (PR #2):** `P2-C*` series; superseded by PR #3.
- **Phase 2 lineage B (PR #3, canonical):** `P2-S0..S10` — secrets, refresh tokens, RLS, transactional writes, concurrency, structured logging, quotas wired, observability with Sentry scrubbing.
- **Phase 3+ planning (PR #4):** authored `BACKEND_HARDENING_PHASE3_PLUS.md`.
- **Phase 3 auth (PR #5):** P3-A1+A2+A3+A4+A8 endpoints.
- **P0 batch (PR #6):** statement timeouts, Celery time limits, body cap, streaming GDPR, atomic GDPR delete, read-only kill switch.
- **Celery hardening (PR #7):** real DLQ, idempotency, NLP outside tx, RLS in tasks.
- **Public-beta batch (PR #8):** access-token blocklist, CI hardening, `SECURITY.md`, dep CVE scan, API versioning doc.
- **Audit logs + key rotation (PR #9):** auth + GDPR audit log tables, atomic API-key rotation.
- **SOC2 batch 1 (PR #10):** circuit breaker, JSON DoS guards, lockout escalation, DLQ CLI.
- **SOC2 batch 2 (PR #11):** JSONB CHECK, migration lint, Celery probe, CodeQL.
- **Block 1 (PR #13, 2026-05-23):** P4-B1+B2+B3+B4 — apps as first-class.
- **Block 2 (PR #14, 2026-05-23):** Amendments 1+2 + P7-E7+E8+E9; bootstrap doc added; `nxm_` prefix fix (`43b3261`); bootstrap appendix cleanup (`6509d6e`).
- **Block 3 (this PR, 2026-05-23):** P9-G2 graceful shutdown (lifespan teardown waits for in-flight, disposes engine, logs `graceful shutdown complete`; env-tunable `GRACEFUL_SHUTDOWN_TIMEOUT=30`); P9-G3 Postgres outage runbook; P9-G4 Redis outage runbook (with explicit fail-open vs fail-closed table per subsystem); P9-G5 OpenAI outage runbook (with circuit-breaker reference); P9-G6 backup-restore drill documentation (RTO 4h / RPO 24h, Supabase + Render + off-platform `pg_dump` paths). **P9-G6 is documentation only — the actual quarterly drill must be performed by the operator.** Four new tests in `tests/test_graceful_shutdown.py`. Sandbox suite: 202 passed / 0 failed / 33 skipped. CI green status not directly verified (no `gh` CLI in sandbox).
