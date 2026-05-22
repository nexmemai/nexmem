# Contributing to NexMem

This file describes the operational expectations the backend ships with.
For repository-level docs see [`PROJECT_STATUS.md`](./PROJECT_STATUS.md);
for security incident response see
[`docs/SECRET_INCIDENT_RUNBOOK.md`](./docs/SECRET_INCIDENT_RUNBOOK.md).

## Required CI checks (recommended branch-protection rules)

The `backend/hardening-phase2` work made CI's signal honest. The
`main` branch should be protected so a PR cannot merge unless every
required check is green.

In GitHub → Settings → Branches → Branch-protection rule for `main`:

- [x] **Require status checks before merging.**
- [x] **Require branches to be up to date before merging.**
- [x] **Required status checks** (exact names from `.github/workflows/ci.yml`):
  - `secret-scan`
  - `unit-tests`
  - `integration-tests`
  - `security-audit`
- [x] **Require linear history** (no merge commits).
- [x] **Restrict who can push to matching branches** — admins only.
- [x] **Require pull-request reviews before merging** — at least 1.
- [x] **Disable allow-force-pushes** for `main`.
  Force-pushing to `main` is reserved for the secret-incident
  runbook only and requires the protection to be temporarily lifted
  by an admin.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Demo-mode unit tests (no DB / no Redis):
pytest tests --ignore=tests/integration

# Integration tests (requires Postgres + Redis service containers
# matching the CI job; see .github/workflows/ci.yml):
RUN_DB_TESTS=1 \
DEMO_MODE=false \
SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/nexmem_test \
REDIS_URL=redis://localhost:6379/0 \
pytest tests/integration

# Repo-wide secret scan:
python scripts/scan_secrets.py
```

## Pre-commit hook (optional, recommended)

```bash
# .git/hooks/pre-commit
#!/usr/bin/env bash
set -euo pipefail
python "$(git rev-parse --show-toplevel)/scripts/scan_secrets.py" --quiet
```

Or via [`pre-commit`](https://pre-commit.com/):

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: scan-secrets
        name: scan-secrets
        entry: python scripts/scan_secrets.py --quiet
        language: system
        pass_filenames: false
```

## Phase-gating philosophy

- **Phase 1** (PR #1) closed the critical issues that kept the
  backend out of private beta:
  hardcoded credentials, production fail-fast, destructive
  migration guard, quota enforcement, demo-mode auth bypass,
  real CI integration tests.
- **Phase 2** (this branch) closes the remaining HIGH risks:
  partial-write atomicity, app-scope consistency, RLS on auth-tier
  tables, refresh-token revocation, migration race, cold-start
  hygiene, observability discipline, CI truthfulness.
- **Phase 3+** (post-private-beta) addresses:
  billing, audit log, multi-region, MFA / SSO, scope enforcement.
  Tracked in [`BACKEND_RISKS.md`](./BACKEND_RISKS.md) under MEDIUM.

Each phase ships as a single bisectable PR with an `P{n}-C{m}`
commit prefix. Out-of-scope items must be explicitly listed in the
phase document and reviewed before merge.
