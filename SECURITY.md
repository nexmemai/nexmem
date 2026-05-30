# Security Policy

## Reporting a vulnerability

If you believe you have found a security issue in Nexmem, please
report it privately to **security@nexmem.ai** rather than opening a
public GitHub issue or pull request.

For sensitive reports, please use PGP. The key fingerprint is
published at <https://nexmem.ai/.well-known/security.txt>.

When you report, please include:

* A clear, concise description of the issue.
* Steps to reproduce, or a proof-of-concept where possible.
* The version / commit SHA you tested against.
* Whether you believe the issue affects the hosted service, the
  self-hosted distribution, or both.
* Any disclosure timeline you would like us to honour.

We commit to:

* Acknowledging your report within **2 business days**.
* Providing an initial assessment (severity + intended fix window)
  within **5 business days**.
* Releasing a fix and crediting you (if you wish) once the issue is
  resolved.

## Scope

In scope:

* The Nexmem API service (`app/`).
* The Nexmem Python SDK (`nexmem-py/`).
* The Nexmem JavaScript SDK (`nexmem-js/`).
* The Nexmem MCP server (`nexmem-mcp/`).
* The deployment configuration (`render.yaml`, `Dockerfile`,
  `docker-compose*.yml`).

Out of scope:

* The Streamlit dashboard at `frontend/` (currently operator-only).
* The marketing site at `nexmem-landing/`.
* Vulnerabilities in third-party dependencies — please report those
  upstream, then let us know if Nexmem is affected.

## Supported versions

We are pre-1.0; security fixes ship to `main` and are deployed to
the hosted service as soon as the fix is validated. Self-hosted
operators should rebase off `main` regularly until we cut a 1.0
release.

## Hardening posture

The backend follows the staged hardening plan in
`BACKEND_HARDENING_PHASE3_PLUS.md`. Each phase ships with a
documented set of acceptance criteria and a corresponding test
suite under `tests/`. The live state of every risk is tracked in
`BACKEND_RISKS.md`.
