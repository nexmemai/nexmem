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

## Decision table (all audited advisories)

| Package | Advisory IDs | Current | Available fix | Blocker | Decision |
|---|---|---|---|---|---|
| python-jose[cryptography] | PYSEC-2024-232, PYSEC-2024-233, PYSEC-2025-185 | 3.3.0 | 3.4.0 | none | **fixed** (→ 3.4.0) |
| python-dotenv | GHSA-mf9w-mj56-hr94 | 1.0.1 | 1.2.2 | none | **fixed** (→ 1.2.2) |
| sentry-sdk[fastapi] | GHSA-g92j-qhmh-64v2 (CVE-2024-40647) | 2.1.1 | 2.8.0 | none | **fixed** (→ 2.8.0) |
| starlette | PYSEC-2026-161, GHSA-2c2j-9gv5-cj73, GHSA-f96h-pmfr-66vw | 0.38.6 | 0.40.0 / 0.47.2 / 1.0.1 | transitive; `fastapi==0.115.0` pins starlette `<0.42`; also constrained by `slowapi==0.1.9` + `prometheus-fastapi-instrumentator` | **needs major bump** (FastAPI upgrade) |
| protobuf | GHSA-7gcm-g887-7qv7 | 4.25.9 | 5.29.6 | transitive via ML stack (sentence-transformers/transformers); protobuf 5.x can break the ML pipeline | **needs major bump** (ML stack) |
| transformers | PYSEC-2025-217, GHSA-69w3-r845-3855 | 4.57.6 | 5.0.0rc3 (one advisory: no stable fix) | transitive via sentence-transformers; fix is a pre-release | **deferred** (no stable fix / ML retest) |
| pyasn1 | GHSA-jr27-m4p2-rc6r | 0.4.8 | 0.6.3 | `python-jose 3.4.0` pins `pyasn1<0.5.0,>=0.4.1` → clean resolve REJECTS 0.6.3 (verified: ResolutionImpossible) | **impossible** without dropping/replacing python-jose |
| ecdsa | GHSA-wj6h-64fc-37mp | 0.19.2 | (none released) | transitive via python-jose[cryptography]; no upstream fix exists | **impossible** (no fix) |

Decisions legend: **fixed** = bumped + verified here; **needs major bump** =
framework upgrade required, own PR; **deferred** = no stable fix / heavy retest;
**impossible** = cannot fix without removing the constraining package.

## Audit results

| Run | Vulnerabilities | Packages |
|---|---|---|
| Before (main @ 7a02202) | 12 | 7 (python-jose, python-dotenv, sentry-sdk, protobuf, starlette, transformers, ecdsa) |
| After this branch's 3 bumps | **8** | **5** (protobuf, pyasn1, starlette, transformers, ecdsa) |

- **5 CVEs resolved:** python-jose ×3, python-dotenv ×1, sentry-sdk ×1.
- **8 remain.** Note `pyasn1` is *newly surfaced* by the python-jose 3.3→3.4 bump
  (net packages went 7→5 but a new transitive advisory appeared); it is
  **impossible** to clear while pinned to python-jose.
- `dependency-audit` still exits non-zero on this branch. Honest and expected,
  not suppressed.

## Safest next upgrade path (NOT landed here — too large for this PR)

1. **FastAPI/starlette PR (separate):** bump `fastapi` to a release whose
   starlette floor is ≥0.40 (ideally ≥0.47.2 to clear all three starlette
   advisories), re-pinning `starlette` accordingly, and re-validate
   `slowapi==0.1.9` + `prometheus-fastapi-instrumentator==7.0.0` compatibility
   (both ride on starlette/fastapi). Requires the full unit + integration suite.
2. **ML-stack PR (separate):** bump `sentence-transformers` to a line that
   allows `protobuf>=5.29.6` and a fixed/stable `transformers`; re-run the
   ML-path tests (`RUN_ML_TESTS=1`). Cannot be validated in this Windows
   sandbox (spaCy / sentence-transformers do not install reliably here) — must
   be done in CI / a Linux env.
3. **python-jose → pyjwt migration (separate, already a P3 item in
   BACKEND_RISKS.md):** the only way to clear `pyasn1` and `ecdsa`, since both
   are gated by python-jose's constraints / lack of fixes.

These are intentionally NOT attempted in this PR: each is a framework-level
change needing a real test environment. No incompatible pins were forced.

## Decision package — what the project owner must decide

1. **Is `dependency-audit` a REQUIRED status check on `main`?** (GitHub
   branch-protection setting; cannot be read from this workspace.)
   - If **required**: PR #24 must NOT merge until the audit passes — which
     means the FastAPI + ML-stack + jose-migration PRs above must land first.
     That is a large body of work; consider whether `dependency-audit` should
     be temporarily marked non-required while those PRs are sequenced.
   - If **non-blocking**: PR #24 (CI-truth + SSL fixes) can merge now with the
     red `dependency-audit` documented as a tracked exception; this branch's
     3 bumps can merge to shrink 12→8; the remaining 8 are scheduled as above.
2. **Risk acceptance for the remaining 8 CVEs** during private beta: all are
   transitive (starlette/protobuf/transformers) or unfixable-in-place
   (pyasn1/ecdsa). None is a known RCE in our usage path, but the owner should
   record an explicit accept-for-beta decision (cross-reference BACKEND_RISKS.md).

## Verification on this branch

- 3 bumped packages install cleanly against the existing stack; app imports OK;
  JWT create/decode round-trip OK with jose 3.4.0.
- Unit suite: **264 passed / 0 failed / 33 skipped** (verified, demo mode).
- `pip install -r requirements.txt` full resolve incl. ML deps: **UNVERIFIED**
  locally (sandbox limitation) — CI is the real check.
- A `pyasn1==0.6.3` pin was tried and **reverted**: it makes the resolve
  impossible against python-jose 3.4.0. No incompatible pin was committed.
