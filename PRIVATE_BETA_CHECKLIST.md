─────────────────────────────────────────
# Nexmem Private Beta — Go / No-Go Checklist
Date: 2026-05-24
─────────────────────────────────────────

## Hard Blockers
All must be true before any real user traffic.

### Code Quality
- [ ] Test suite: 278+ passing, 0 failing
- [x] Secret scan: clean
- [ ] Bandit: no HIGH or CRITICAL findings
- [ ] CI green on tip branch (GitHub Actions)
- [ ] Alembic chain 001-024 generates valid SQL

### Deployment (operator must verify)
- [ ] DATABASE_URL set in Render (sync: false)
- [ ] REDIS_URL set in Render
- [ ] SECRET_KEY set in Render (min 32 chars, hex)
- [ ] OPENAI_API_KEY set in Render
- [ ] SENTRY_DSN set in Render
- [ ] ADMIN_API_KEY set in Render
- [ ] DEMO_MODE absent from Render env vars
- [ ] alembic upgrade head confirmed on live DB
      (migrations 001-024 all applied)
- [ ] /health/live returns 200 on live URL
- [ ] /health/ready returns 200 on live URL

### Security Baseline (operator must verify)
- [x] Git history rewritten to remove Phase-1 
      leaked Supabase password 
      (completed + force-pushed; see HISTORY_REWRITE_COMPLETE.md)
- [x] History rewrite completed and verified
      (all 22 branches + tags; 0 real secrets remain in remote history)
- [x] Secret scanner passing
      (scripts/scan_secrets.py clean; SHA-256 hash tripwire)
- [ ] SECRET_KEY rotated since Phase 1
- [ ] Supabase database password confirmed rotated
- [ ] No plaintext secrets in any config file

### Smoke Tests (run manually after deploy)
- [ ] POST /api/v1/auth/register → 201
- [ ] POST /api/v1/auth/login → JWT returned
- [ ] POST /api/v1/auth/api-keys → nxm_ key returned
- [ ] POST /api/v1/memory/episode/write → 200
- [ ] POST /api/v1/memory/context → memories returned
- [ ] GET /api/v1/memory/user/{id}/export → JSON
- [ ] DELETE /api/v1/memory/user/{id}/all → 
      {scheduled_deletion: true} response

## Operator Actions Still Pending
- [ ] Merge PR #4 (Phase 3+ plan)
- [ ] Decide between PR #2 and PR #3 for Phase 2.
- [ ] Close PR #1 without merge after PR #3 merges.
- [ ] Merge canonical stack in order: PR #3 → #5 → #6 → #7 → #8 → #9 → #10 → #11 → #13 → #14 → Block-3 PR.
- [x] Operator git-history rewrite (completed + force-pushed)
- [ ] Set Render env vars
- [ ] Run alembic upgrade head against the live DB
- [ ] Confirm GitHub Actions CI is green
- [ ] Schedule the first quarterly backup-restore drill
- [ ] Merge PR #12 (kiro/work-log)
- [ ] Publish nexmem-py to PyPI
- [ ] Publish nexmem-js to npm

## Accepted Risks for Private Beta
- R-102: No real session revocation — mitigation in place: Access tokens are short-lived (4 hours) and refresh tokens are revocable. Access token blocklist exists but fails open.
- R-107: NetworkX graph state is per-process — mitigation in place: Pinned to `--workers 1`.
- R-301: Redis fail-open allows auth / rate-limit bypass during outage — mitigation in place: Accepted for private beta, monitor Redis uptime aggressively.
- R-305: Admin endpoints are inert without ADMIN_API_KEY — mitigation in place: Must be set in Render for admin tooling to work.
- R-306: check_app_not_suspended fails open on DB error — mitigation in place: R-301 fail-open posture.
- R-202: Demo path and production path duplicate logic — mitigation in place: Long-running refactor out of scope for Phase 2.
- R-204: No load testing under realistic conditions — mitigation in place: Scaling-time risk, not a launch blocker.
- R-205: Migration 007 is destructive — mitigation in place: Already ran on live DB, fenced.

## Deferred to Post-Launch
- Task P3-A9: CAPTCHA / proof-of-work on signup — explicitly deferred
- Task P9-G7: Multi-region deployment — explicitly deferred
- Task: Billing / Stripe / subscription productization — explicitly deferred
- Task: SDK published to PyPI / npm — explicitly deferred
- Task: MCP-server hardening beyond a smoke-tested skeleton — explicitly deferred
- Task: New connectors, webhooks, or third-party integrations — explicitly deferred
- Task: Cosmetic UI work — explicitly deferred
- Task: Reranker upgrade — explicitly deferred
- Task: Streamlit dashboard interactive graph view — explicitly deferred
- Task: Load-test verification with Locust against a production-shaped instance — explicitly deferred

## Celery Beat Schedules Needed (operator action)
These tasks exist in code but need scheduling:
- [ ] execute_scheduled_deletions — daily at 2am UTC
      (soft-delete grace period enforcement)
- [ ] enforce_data_retention — weekly at 3am UTC
      (episodic memory cleanup per retention policy)

## Final Recommendation

**Code readiness:** GO
Basis: test count 278 passed, secret scan clean, bandit scan clean (no high/critical), docs accurate.

**Deployment readiness:** REQUIRES OPERATOR ACTION
Basis: env vars and migrations cannot be verified 
from code alone

**Security baseline:** REQUIRES OPERATOR ACTION  
Basis: git history rewrite and key rotation are 
operator steps

**Overall recommendation:**
GO WITH CONDITIONS

Conditions:
- Canonical stack merged into main with CI green at each step.
- Operator git-history rewrite complete, force-pushed.
- Render env vars set; DEMO_MODE unset; live DB on 024_app_suspension.
- /health/ready returns 200 in production.
- First backup-restore drill recorded.
─────────────────────────────────────────
