# Dependency CVE Remediation Status

Branch: `chore/dependency-cve-upgrades` (separate from the docs PR #24).
Source: `pip-audit -r requirements.txt --vulnerability-service osv` (12 findings
across 7 packages on `main` @ `7a02202`).

## Fixed in this branch (safe, in-line, direct deps)

| Package | From | To | Advisory | Notes |
|---|---|---|---|---|
| python-jose[cryptography] | 3.3.0 | 3.4.0 | PYSEC-2024-232/233 | direct dep; in-line fix |
| python-dotenv | 1.0.1 | 1.2.2 | GHSA-mf9w-mj56-hr94 | direct dep; in-line fix |
| sentry-sdk[fastapi] | 2.1.1 | 2.8.0 | GHSA-g92j-qhmh-64v2 (CVE-2024-40647) | patched in 2.8.0; stays on 2.x (NOT the 1.45.1 downgrade OSV suggests) |

## Deferred — require framework-level upgrades (NOT done here)

These are transitive and/or need a major bump that carries real
breaking-change risk; each needs its own scoped, fully-tested change.
Listed honestly rather than force-bumped.

| Package | Current | Fix | Why deferred |
|---|---|---|---|
| starlette | 0.38.6 | 0.47.2+ (CVE-2025-54121) / 0.40.0 | **transitive via fastapi==0.115.0** (pins starlette <0.42). Requires a FastAPI major bump + full app retest. |
| protobuf | 4.25.9 | 5.29.6 | transitive via the ML stack (sentence-transformers/transformers). Bumping risks the ML pipeline; needs ML-path retest. |
| transformers | 4.57.6 | 5.0.0rc3 (one advisory has no stable fix) | major (5.x is a pre-release); transitive via sentence-transformers. Needs ML retest. |
| ecdsa | 0.19.2 | (no fix released) | transitive via python-jose[cryptography]; GHSA-wj6h-64fc-37mp has no upstream fix. Mitigation: prefer the cryptography backend; revisit when a fix ships. |

## Verification required on this branch before merge

- `pip install -r requirements.txt` resolves cleanly. **UNVERIFIED locally**
  (this Windows workspace cannot reliably install spaCy/sentence-transformers;
  CI is the real check — see sandbox limitations noted in KIRO_SESSION_BOOTSTRAP.md).
- Full unit suite green (`pytest -m "not slow and not integration"`).
- `dependency-audit` re-run: the 3 fixed packages clear; the 4 deferred ones
  will still report until the framework upgrades land.
- Integration + alembic-roundtrip green (rely on the CI SSL fix in PR #24).

## Recommendation

Merge order: PR #24 (CI truth + SSL + audit-command fix) first, then this
dependency branch once CI validates the 3 bumps, then a separate
FastAPI/ML-stack upgrade PR for the 4 deferred CVEs.
