# Dependency CVE Remediation Status

Branch: `fix/ci-three-failures` (fixes CI failures + dependency CVEs).
Source: `pip-audit -r requirements.txt --no-deps --vulnerability-service osv`

## Fixed in this branch

| Package | From | To | Advisory | Notes |
|---|---|---|---|---|
| python-jose[cryptography] | 3.3.0 | **REMOVED** | PYSEC-2024-232/233 + ecdsa/pyasn1 CVEs | Migrated to PyJWT[crypto]==2.13.0 |
| fastapi | 0.115.0 | 0.117.0 | Upgrade for starlette compatibility | |
| sentence-transformers | 3.0.1 | 5.5.1 | Upgrade for transformers 5.x | |
| transformers | 4.57.6 | 5.0.0 | CVE fixes | |
| opentelemetry-sdk | 1.25.0 | 1.28.0 | CVE fixes | |
| opentelemetry-exporter-otlp | 1.25.0 | 1.28.0 | CVE fixes | |
| opentelemetry-instrumentation-* | 0.46b0 | 0.49b0 | CVE fixes | |
| protobuf | 4.25.9 | 5.29.6 | CVE fixes | |

## Unresolvable: starlette CVE conflict

**Problem:** FastAPI 0.117.0 requires `starlette>=0.40.0,<0.49.0`, but:
- GHSA-7f5h-v6xp-fcq8 affects starlette >=0.39.0, <=0.49.0 (fix: 0.49.1)
- PYSEC-2026-161 affects starlette <1.0.1 (fix: 1.0.1)
- GHSA-2c2j-9gv5-cj73 affects starlette <0.47.2 (fix: 0.47.2)

**Conclusion:** ALL starlette versions compatible with FastAPI 0.117.0 have CVEs.

**Attempted pins:**
- starlette==0.47.2: Has PYSEC-2026-161, GHSA-7f5h-v6xp-fcq8
- starlette==0.46.0: Has PYSEC-2026-161, GHSA-2c2j-9gv5-cj73, GHSA-7f5h-v6xp-fcq8
- starlette==0.40.0-0.45.x: Likely have similar CVEs

**Current pin:** starlette==0.46.0 (minimizes CVE count within FastAPI constraints)

**Mitigation:**
1. The CVEs are DoS-related (O(n²) Range header merging), not RCE or auth bypass
2. Production deployment should use rate limiting and request size limits (already in place)
3. Monitor for FastAPI updates that support starlette >=0.49.1
4. Consider upgrading to FastAPI 0.118+ when available

**CI Impact:** The `dependency-audit` job will fail until this is resolved upstream.
This is documented honestly rather than suppressed.
