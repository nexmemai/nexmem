# Backend Risk Register

**Last updated:** 2026-05-30 (Block 10 merge-prep — post-rewrite).

This document is the source of truth for backend risk. It is updated
on every hardening pass. Do not delete entries; mark them
`RESOLVED`, `MITIGATED`, `ACCEPTED`, or `OPEN`.

Severity scale:
- **P0** — blocks first real-user traffic.
- **P1** — should be fixed before first real-user traffic.
- **300 series** — accepted for private beta, **must fix before public launch.**
- **P2** — known limitation, accepted for private beta (no public-launch deadline).
- **P3** — nice-to-have.

---

## P0 — blockers for first real-user traffic

### R-001 Supabase password leaked in `alembic/env.py` history
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

### R-002 `render.yaml` embedded Supabase pooler URL
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

### R-003 `validate_production` did not enforce
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

### R-004 `current_user.app_id` `AttributeError`
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

### R-005 Quota enforcement was never wired
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

### R-006 Engine is created at import time with hard validator
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

### R-007 Migration race in multi-replica deploy
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

### R-008 RLS context leak in `get_current_user`
- **Status:** **RESOLVED** — fixed by Phase 2 (PR #3).

---

## P1 — should ship before first real-user traffic

### R-101 RLS is only enforced on memory tables
- **Status:** **MITIGATED** — Phase 2 added RLS to api_keys, refresh_tokens, token_usage, users. Future tables need it in the same migration.

### R-102 No real session revocation
- **Status:** **ACCEPTED** — access tokens are short-lived (4 hours) and refresh tokens are revocable. Access token blocklist exists but fails open.

### R-103 Sentry config is unsafe defaults
- **Status:** **RESOLVED** — fixed by Phase 2.

### R-104 Health endpoint does not check Redis
- **Status:** **RESOLVED** — fixed by Phase 2.

### R-105 Multi-step writes are not transactional
- **Status:** **RESOLVED** — fixed by Phase 2.

### R-106 Blocking NLP / embedding work in async routes
- **Status:** **MITIGATED** — per-event-loop semaphore is retained. Global cap needed.

### R-107 NetworkX graph state is per-process
- **Status:** **ACCEPTED** — pinned to `--workers 1`.

### R-108 Read quotas are not enforced
- **Status:** **RESOLVED** — fixed by Phase 2.

### R-109 Token expiry test missing
- **Status:** **RESOLVED** — fixed by Phase 2.

### R-110 Logging middleware was unstructured
- **Status:** **RESOLVED** — fixed by Phase 2.

---

## 300 series — accepted for private beta, must fix before public launch

### R-301 Redis fail-open allows auth / rate-limit bypass during outage
- **Severity:** HIGH.
- **Affected subsystems:**
  - `app/core/brute_force.py` — per-(email, IP) login lockout. `_get_redis()` returns `None` on connection error and the code falls back to a thread-local in-memory store.
  - `app/core/rate_limit.py` — slowapi limiter (per-route + per-user caps). slowapi falls back to its in-memory storage when its Redis client raises.
  - `app/core/token_blocklist.py` — access-token blocklist (P3-A5). `is_revoked` returns `False` (i.e. "token is not on the blocklist") on Redis error.
  - `app/core/quotas.py` — fails closed (HTTP 503) when REDIS_URL is set but unreachable.
  - Celery — hard-fails when Redis is down.
- **Status:** **ACCEPTED** — accepted for private beta.

### R-302 TOTP session tokens have no dedicated rate limit
- **Severity:** P2.
- **Status:** **OPEN** — POST /auth/totp/complete-login can be brute-forced.

### R-303 execute_scheduled_deletions requires Celery Beat schedule
- **Severity:** P2.
- **Status:** **OPEN** — Celery Beat schedule not configured yet (operator action).

### R-304 enforce_data_retention requires Celery Beat schedule
- **Severity:** P2.
- **Status:** **OPEN** — Celery Beat schedule not configured yet (operator action).

### R-305 Admin endpoints are inert without ADMIN_API_KEY
- **Severity:** P3.
- **Status:** **ACCEPTED** — Must be set in Render for admin tooling to work.

### R-306 check_app_not_suspended fails open on DB error
- **Severity:** P3.
- **Status:** **ACCEPTED** — Write proceeds even if suspension check fails due to database error (R-301 fail-open posture).

---

## P2 — accepted for private beta

### R-201 Full git-history rewrite required
- **Status:** **RESOLVED** — git-filter-repo rewrite completed across all 22 branches + tags and force-pushed (2026-05-30). The leaked Supabase password, project ref, and GitHub PAT no longer appear in remote history. Rotation was done separately. See `HISTORY_REWRITE_COMPLETE.md`. Residual operator action: collaborators must delete old clones and re-clone.

### R-202 Demo path and production path duplicate logic
- **Status:** **ACCEPTED** — long-running refactor out of scope for Phase 2.

### R-203 No formal "App" model
- **Status:** **RESOLVED** — fixed by Block 1 (PR #13) apps table.

### R-204 No load testing under realistic conditions
- **Status:** **ACCEPTED** — scaling-time risk, not a launch blocker.

### R-205 Migration 007 is destructive
- **Status:** **ACCEPTED** — already ran on live DB, fenced.

### R-206 No backup / restore documentation
- **Status:** **RESOLVED** — fixed by Block 3 (P9-G6).

---

## Block 10 — merge-prep resolutions (2026-05-30)

### R-207 GitHub PAT present in git history
- **Severity:** P0 (credential exposure).
- **Status:** **RESOLVED** — the GitHub PAT (and the embedded-token remote URL that caused it) were purged by the history rewrite and the token rotated. Remote URL is now token-free. Prescan of rewritten history shows 0 real `ghp_` tokens (the only `ghp_` matches are the scanner's own detection regex + doc references).

### R-208 Supabase DB password in git history
- **Severity:** P0 (credential exposure).
- **Status:** **RESOLVED** — purged by the history rewrite (both URL-encoded and decoded forms, plus the project ref and pooler hostnames) and the DB password rotated. Prescan shows 0 occurrences in remote history.

### R-209 Secret scanner broken by the history rewrite
- **Severity:** P1 (CI-blocking).
- **Status:** **RESOLVED** — the rewrite replaced the cleartext incident value inside `scripts/scan_secrets.py` with a placeholder that was not valid regex, crashing the scanner (`re.error: nothing to repeat`) on all branches. Replaced the two cleartext-regex tripwires with a SHA-256 hash-based tripwire (`INCIDENT_TRIPWIRE_HASHES`) so detection survives without re-committing the secret. Scanner runs clean; `tests/test_secret_scan.py` passes. NOTE: this fix must be present on a branch before its CI can pass.

---

## P3 — nice to have

- Migrate from `python-jose` to `pyjwt` (jose is in maintenance mode).
- Add `bandit` to CI (already in dev deps; not yet wired).
- Add `mypy` strict mode for `app/core/`.
- Replace `passlib[bcrypt]==1.7.4` with a maintained alternative.
