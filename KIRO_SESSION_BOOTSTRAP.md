# Kiro Session Bootstrap

> **Read this file at the start of every new Kiro session, before any task is executed.** This is the single source of truth for project state, rules, and context.

═══════════════════════════════════════════
SECTION 1 — PROJECT IDENTITY
═══════════════════════════════════════════

## Project Identity

- **Name:** Nexmem
- **Repo:** github.com/nexmemai/nexmem
- **Stack:** FastAPI, async SQLAlchemy 2.0, PostgreSQL 16 + pgvector, Celery + Redis, spaCy + sentence-transformers (384D MiniLM), JWT + SHA-256 API keys, Alembic migrations, Render deployment.
- **API key prefix:** `nxm_` (never `mem_` or `sk-`).
- **Vector dimension:** 384D everywhere (never 1536D).
- **Python version:** 3.11+.

═══════════════════════════════════════════
SECTION 2 — ABSOLUTE RULES (never break these)
═══════════════════════════════════════════

## Absolute Rules

Read these before every single task. No exceptions.

1. NEVER merge anything to `main` without explicit user confirmation saying the exact words **"merge to main"**.
2. NEVER start a new block without user confirmation of the previous block.
3. NEVER use `Base.metadata.create_all()` — Alembic migrations only.
4. NEVER hardcode secrets — always `os.getenv()` or `settings.*`.
5. NEVER call `nlp(text)` or `model.encode()` directly inside `async def` — always use `run_in_executor` with `NLP_SEMAPHORE`.
6. EVERY DB query must filter by `.where(Model.user_id == current_user.id)`.
7. EVERY new table needs `user_id` + `app_id` columns with indexes.
8. NEVER skip `pytest` before committing — tests must pass first.
9. NEVER mark a task DONE unless a test or explicit verification confirms it.
10. NEVER rewrite git history without explicit user instruction.
11. NEVER force-push without explicit user instruction.
12. NEVER invent completed work — if you cannot verify it, mark **UNVERIFIED**.
13. Stack every new PR on the previous block tip — never branch from `main`.
14. One PR per block — stop after each block and wait for confirmation.
15. API key prefix is `nxm_` not `mem_` not `sk_`.

═══════════════════════════════════════════
SECTION 3 — CURRENT PR STACK
═══════════════════════════════════════════

## Current PR Stack

> **Format note (honesty):** The bootstrap-file prompt that created this section was truncated mid-sentence after "Format:". The format used below was chosen by Kiro at file-creation time and is open to revision. Future sessions should ask the user to confirm the format before reformatting this section.
>
> **State source (verified at write time):** `git branch --show-current`, `git log --oneline`, `git branch -a`, `git rev-parse HEAD` — run on 2026-05-23 from `chore/p7-rate-limits-error-hygiene`.

### Canonical hardening stack (oldest → newest)

| Order | PR | Branch | Head SHA | Title (abbreviated) | Status |
|---|---|---|---|---|---|
| 0 | — | `main` | `56afcdb` | One non-Kiro commit (`chore: remove misconfigured vercel.json`) | Default branch |
| 1 | **#3** | `chore/p2-backend-hardening` | `4bdd8e5` | Phase 2 hardening (P2-S0..S10) — secrets, sessions, RLS, transactional writes, quotas, observability | open |
| 2 | **#5** | `chore/p3-auth-hardening` | `cebc873` | Phase 3 auth (P3-S0..S3 ships A1+A2+A3+A4+A8) | open |
| 3 | **#6** | `chore/p5-p6-p7-prod-hardening` | `861f8ba` | P5/P6/P7/P9 P0 batch (statement timeouts, Celery limits, body cap, streaming GDPR, read-only mode) | open |
| 4 | **#7** | `chore/p6-celery-hardening` | `77bd459` | P6-D1+D5+D6+D9 (real DLQ, idempotency lock, NLP outside tx, RLS in tasks) | open |
| 5 | **#8** | `chore/before-public-beta-batch` | `5ecb1f6` | P3-A5+P5-C5+P10-H5+H6+P12-J6 (blocklist, CI, security, versioning) | open |
| 6 | **#9** | `chore/before-billing-audit-logs` | `06f89af` | P10-H1+H2+P3-A10 (audit logs + atomic API key rotation + test isolation) | open |
| 7 | **#10** | `chore/before-soc2-polish` | `d3e000a` | P6-D7+P7-E6+P3-A7+P11-I5 (circuit breaker, JSON DoS guard, lockout escalation, DLQ CLI) | open |
| 8 | **#11** | `chore/before-soc2-batch-2` | `9735b50` | P5-C9+P5-C6+P8-F7+P10-H7 (JSON CHECK, migration lint, Celery probe, CodeQL) | open |
| 9 | **#13** | `chore/p4-apps-first-class` | `65835ca` | Block 1: P4-B1+B2+B3+B4 (apps as first-class + app-level RLS) | open |
| 10 | **#14** | `chore/p7-rate-limits-error-hygiene` | `09c7f32` | Block 2: Amendments 1+2 + P7-E7+E8+E9 (app_id RLS plumbing, per-route + per-user limits, error hygiene) | open (current tip) |

### Side / non-canonical PRs

| PR | Branch | Head SHA | Purpose | Status |
|---|---|---|---|---|
| #1 | `backend/hardening-private-beta` | `d65c7b5` | Phase 1 (C1..C12) — superseded by PR #3. Recommended: close without merge after PR #3 lands. | open |
| #2 | `backend/hardening-phase2` | `c812eea` | Alternative Phase 2 lineage (P2-C* IDs). PR #3 is canonical. Recommended: close without merge. | open |
| #4 | `docs/backend-hardening-phase3-plan` | `0e1aa65` | `BACKEND_HARDENING_PHASE3_PLUS.md` (10 future phases, ~80 IDs). Docs-only, can merge any time. | open |
| #12 | `kiro/work-log` | (head of branch) | `KIRO_WORK_LOG.md` — verifiable Kiro work history snapshot. | open |

### Alembic migration head

`020_engrams_app_id` (added in PR #14). Chain: `… → 016 → 017 → 018 → 019 → 020`. Single head verified by `alembic upgrade head --sql` against the offline driver.

### Block sequence completed

- **Block 1** → PR #13 (Phase 4 data model: `apps` table, `api_keys.app_id` FK, `/apps/register` rate limit, app-level RLS on the 5 memory tables).
- **Block 2** → PR #14 (Amendment 1: wire `app.current_app_id`; Amendment 2: `engrams.app_id` + RLS; P7-E7 per-route limits on /auth/login + /memory/episode/write + /rag/chat; P7-E8 per-user `key_func`; P7-E9 generic error responses, 6 sites cleaned).
- **Block 3 onward:** **awaiting user spec.** Do NOT pick a block from a candidate list without explicit confirmation.

### Next block (placeholder)

When the user defines Block 3, append a row to the canonical-stack table here in the same format. The stack-on-tip for Block 3 is **`chore/p7-rate-limits-error-hygiene` (`09c7f32`, PR #14)** unless the user says otherwise.

═══════════════════════════════════════════
APPENDIX — SESSION HONESTY FOOTER
═══════════════════════════════════════════

The user's instruction was "exactly these sections" (1, 2, 3). This footer is **not** a 4th section; it is metadata about the file itself, written so future sessions don't silently violate Rule #12 ("never invent completed work").

### Discrepancies the future session must reconcile

- **Rule #15 vs codebase.** Section 2 rule #15 says the API key prefix is `nxm_`. The current canonical-stack code in `app/core/security.py` generates `"mem_" + secrets.token_urlsafe(32)` (verified at write time with `grep`). Treat this as **UNVERIFIED** until the user clarifies one of:
  - "`mem_` is legacy, migrate to `nxm_`" → write a migration + grace-period rotation.
  - "`mem_` is correct, rule #15 is a typo" → update the bootstrap file's rule #15.
  Do not pick on your own.

- **Section 3 format truncation.** The user's prompt for Section 3 was cut after "Format:" by an embedded steering-message footer. The table format used here is Kiro's best guess. If the user later supplies a different format, replace the section in a follow-on commit on a fresh branch — do not amend this commit.

### Sandbox limitations encountered in the original Block 1 / Block 2 sessions (relevant to test posture in CI vs locally)

- The container runtime (rootless podman + crun) cannot reliably keep Postgres up; host port forwarding fails. So `alembic upgrade head` against a real DB and `pytest --run-db` cannot be done in the Kiro sandbox. **CI is the real verification.** Future sessions: do not waste cycles trying to spin Postgres locally.
- `pip install spacy sentence-transformers` cannot complete in the sandbox because of build-time deps. ML-dependent tests skip. Same as above — CI installs the full `requirements.txt` cleanly.
- 8 tests in `tests/test_dlq_admin_cli.py` and `tests/test_p6_celery_hardening.py` fail in the sandbox due to Celery/Redis mocking versions. Verified to be pre-existing on the canonical tip `9735b50` before any Block 1 / Block 2 changes were applied. Do not treat them as regressions.

### One-line current-state summary (overwrite this every session)

> 2026-05-23 — Block 2 just shipped as PR #14 stacked on PR #13. Canonical stack is 8 PRs deep (PR #3 → #5 → #6 → #7 → #8 → #9 → #10 → #11 → #13 → #14). Zero merges to `main`. Awaiting Block 3 spec from user.
