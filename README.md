# NexMem — Persistent AI Memory Layer

A persistent, cross-platform memory system for AI agents and LLMs.
NexMem models memory like human cognition — episodic, semantic,
procedural, and associative — and exposes it through a hardened FastAPI
backend with JWT + API-key auth, Row-Level Security, rate limiting, and
multi-app scoping.

> **Status:** pre-1.0 private beta · deployed on Render · schema
> managed by Alembic (25 migrations)

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                       Streamlit Dashboard                             │
│  ┌──────────────┐ ┌───────────────────┐ ┌──────────────────────────┐ │
│  │ Memory Graph  │ │  Memory Chat      │ │  Live Memory Feed        │ │
│  │ (NetworkX)    │ │  (RAG + GPT-4o)   │ │  (real-time updates)     │ │
│  └──────────────┘ └───────────────────┘ └──────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│                        FastAPI Backend                                │
│                                                                       │
│  Auth · Episodic · Semantic · Procedural · Graph · RAG · Memory       │
│  Apps · GDPR · TOTP · Admin · Health                                  │
│                                                                       │
│  Middleware: CORS · Rate Limit · Body Cap · JSON Shape Guard          │
│              Read-Only Kill Switch · Structured Logging                │
│                                                                       │
│  Workers: Celery (consolidation · data retention · engram processing) │
└───────────────────────────────────────────────────────────────────────┘
                                │
               ┌────────────────┼────────────────┐
               ▼                ▼                ▼
   ┌──────────────────┐ ┌─────────────┐ ┌──────────────┐
   │  PostgreSQL 16   │ │   Redis 7   │ │  OpenAI API  │
   │  + pgvector      │ │  rate limit │ │  embeddings  │
   │  + RLS policies  │ │  quotas     │ │  GPT-4o chat │
   │  + Alembic       │ │  Celery     │ │              │
   └──────────────────┘ └─────────────┘ └──────────────┘
```

---

## Memory Types

| Type | Description | Storage | Decay |
|------|-------------|---------|-------|
| 🧠 **Episodic** | Time-stamped conversation history | PostgreSQL | Configurable (default 365 days) |
| 🔍 **Semantic** | Vector embeddings for meaning search | pgvector (384-dim, `all-MiniLM-L6-v2`) | Never |
| ⚙️ **Procedural** | User preferences, settings, workflows | JSONB | Never |
| 🕸️ **Associative** | Knowledge graph relationships | Nodes + Edges (NetworkX in-memory) | Never |

---

## Quick Start

### Option 1: Docker Compose (recommended for local dev)

```bash
git clone https://github.com/nexmemai/nexmem.git
cd nexmem

# Set your OpenAI key (optional — demo mode works without it)
export OPENAI_API_KEY=sk-...

docker-compose up --build

# Backend:    http://localhost:8000
# Dashboard:  http://localhost:8501
# PgBouncer:  localhost:6432
```

### Option 2: Manual Setup

```bash
# 1. Clone and install
git clone https://github.com/nexmemai/nexmem.git
cd nexmem
python -m venv .venv && .venv/Scripts/activate   # Windows
# python -m venv .venv && source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 2. Configure
cp .env.example .env.local
# Edit .env.local — set DATABASE_URL, SECRET_KEY, OPENAI_API_KEY

# 3. Run migrations
alembic upgrade head

# 4. Start the API
uvicorn app.main:app --reload --port 8000

# 5. (Optional) Start Celery worker
celery -A app.celery_app.celery_app worker --loglevel=info

# 6. (Optional) Start the Streamlit dashboard
cd frontend && pip install -r requirements.txt
streamlit run app.py
```

### Option 3: Demo Mode (no database required)

```bash
# DEMO_MODE=true is the default — no Postgres needed
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### SDK Quickstarts

Once the backend is running, try one of the SDK quickstarts. Both
register a throwaway demo user, mint an `nxm_`-prefixed API key, and
exercise `remember` + `recall` end-to-end:

- **Python:** [`examples/python_quickstart.py`](./examples/python_quickstart.py)
- **JavaScript:** [`examples/javascript_quickstart.mjs`](./examples/javascript_quickstart.mjs)
- **Prerequisites:** [`examples/README.md`](./examples/README.md)

SDK source lives under [`nexmem-py/`](./nexmem-py/README.md) and
[`nexmem-js/`](./nexmem-js/README.md). Neither is published to
PyPI/npm yet — install from this repo for now. An MCP server is at
[`nexmem-mcp/`](./nexmem-mcp/README.md).

---

## API Endpoints

### Authentication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/auth/register` | POST | Register a new user |
| `/api/v1/auth/login` | POST | Obtain access + refresh tokens |
| `/api/v1/auth/refresh` | POST | Rotate refresh token |
| `/api/v1/auth/totp/setup` | POST | Enable TOTP 2FA |
| `/api/v1/auth/totp/verify` | POST | Verify TOTP code |

### Memory Operations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/agents/{user_id}/episodes` | GET/POST | List / create episodic memories |
| `/api/v1/agents/{user_id}/semantics` | GET/POST | List / create semantic memories |
| `/api/v1/agents/{user_id}/semantic/search` | POST | Vector similarity search |
| `/api/v1/agents/{user_id}/procedural/settings` | GET/POST | Get / upsert procedural memory |
| `/api/v1/agents/{user_id}/graph/nodes` | GET/POST | List / create knowledge nodes |
| `/api/v1/agents/{user_id}/graph/edges` | GET/POST | List / create knowledge edges |
| `/api/v1/agents/{user_id}/graph/path` | POST | Find path between nodes |
| `/api/v1/rag/chat` | POST | RAG-enhanced chat |
| `/api/v1/memory/stats/{user_id}` | GET | Memory statistics |
| `/api/v1/memory/remember` | POST | Unified write (auto-classify) |
| `/api/v1/memory/recall` | POST | Unified read (multi-type search) |

### Platform

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/apps/register` | POST | Register an application |
| `/api/v1/gdpr/export` | POST | GDPR data export |
| `/api/v1/gdpr/erase` | DELETE | GDPR right-to-erasure |
| `/api/v1/admin/*` | Various | Admin panel (gated by `X-Admin-Key`) |
| `/health/live` | GET | Liveness probe |
| `/health/ready` | GET | Readiness probe |
| `/metrics` | GET | Prometheus metrics (bearer-protected) |

---

## Project Structure

```
nexmem/
├── app/
│   ├── main.py                # FastAPI application + lifespan
│   ├── config.py              # Pydantic-settings (440+ config knobs)
│   ├── database.py            # Async SQLAlchemy engine + RLS context
│   ├── celery_app.py          # Celery application
│   ├── demo_db.py             # In-memory demo storage
│   ├── tasks.py               # Celery tasks (consolidation, retention)
│   ├── core/
│   │   ├── security.py        # JWT encode/decode, password hashing
│   │   ├── deps.py            # FastAPI dependencies (get_current_user)
│   │   ├── rate_limit.py      # Slowapi + Redis rate limiting
│   │   ├── brute_force.py     # Login brute-force protection
│   │   ├── circuit_breaker.py # OpenAI circuit breaker
│   │   ├── token_blocklist.py # JWT revocation (Redis-backed)
│   │   ├── admin_auth.py      # X-Admin-Key authentication
│   │   └── ...
│   ├── middleware/
│   │   ├── body_size_limit.py # Request body cap (5 MB default)
│   │   ├── json_shape_guard.py# JSON depth/node count limits
│   │   ├── read_only_mode.py  # Kill switch for writes
│   │   └── logging.py         # Structured request logging
│   ├── models/                # SQLAlchemy ORM (User, Memory, App, ...)
│   ├── schemas/               # Pydantic request/response models
│   ├── routers/               # 12 FastAPI routers (auth, memory, admin, ...)
│   └── services/              # Embedder, LLM, retriever, reranker, ...
├── alembic/
│   └── versions/              # 25 migrations (001..024 + day3_auth)
├── tests/                     # 47 test modules (unit + integration)
├── scripts/
│   ├── scan_secrets.py        # CI credential scanner
│   ├── lint_migrations.py     # Migration safety lint
│   ├── nexmem_admin.py        # CLI admin tool
│   └── ...
├── frontend/                  # Streamlit dashboard
├── nexmem-py/                 # Python SDK
├── nexmem-js/                 # JavaScript/TypeScript SDK
├── nexmem-mcp/                # MCP server for AI agents
├── examples/                  # Quickstart scripts
├── docs/                      # SLO, runbooks, data retention, API versioning
├── .github/workflows/ci.yml   # CI/CD pipeline (8 jobs)
├── docker-compose.yml         # Local development stack
├── docker-compose.prod.yml    # Production compose
├── render.yaml                # Render deployment blueprint
├── Dockerfile                 # Multi-stage production image
├── requirements.txt           # Production dependencies
├── requirements-dev.txt       # Dev/test dependencies
└── pytest.ini                 # Test configuration + markers
```

---

## CI / CD Pipeline

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs **8 jobs**
on every push to `main` and every PR:

| Job | What it does |
|-----|-------------|
| `secret-scan` | Scans for leaked credentials via `scripts/scan_secrets.py` |
| `migration-lint` | Checks new Alembic migrations for unsafe patterns |
| `lint-and-test` | Flake8 lint + unit tests (demo mode, no DB) + coverage |
| `integration-tests` | Postgres + Redis service containers → Alembic `upgrade head` → `pytest -m integration` |
| `security-audit` | Bandit SAST scan |
| `dependency-audit` | `pip-audit` against production deps |
| `alembic-roundtrip` | `upgrade head → downgrade base → upgrade head` against disposable Postgres |
| `docker-build` | Docker image build (push-to-main only) |

---

## Environment Variables

See [`.env.example`](./.env.example) for the full list. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_MODE` | `true` | In-memory storage (no DB required) |
| `DATABASE_URL` | — | PostgreSQL connection string |
| `SECRET_KEY` | dev placeholder | JWT signing key (≥ 32 chars) |
| `OPENAI_API_KEY` | `sk-placeholder` | OpenAI API key |
| `REDIS_URL` | — | Redis for rate limiting / Celery |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `SENTRY_DSN` | — | Sentry error tracking |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OpenTelemetry collector |
| `ADMIN_API_KEY` | — | Static key for `/api/v1/admin/*` |
| `DB_REQUIRE_SSL` | `true` | Require SSL for Postgres |

---

## Deployment

### Render (primary)

The [`render.yaml`](./render.yaml) blueprint deploys four services:

1. **nexmem-api** — Web service (uvicorn, 1 worker, advisory-locked migrations at boot)
2. **nexmem-celery-worker** — Background task worker
3. **nexmem-celery-beat** — Periodic task scheduler
4. **nexmem-redis** — Redis instance

See [`DEPLOY.md`](./DEPLOY.md) for step-by-step instructions.

### Docker

```bash
# Production build
docker build -t nexmem-api .
docker run -p 8000:8000 \
  -e DATABASE_URL=... \
  -e SECRET_KEY=... \
  nexmem-api
```

---

## Security

- **Auth:** JWT access + refresh tokens, TOTP 2FA, API keys (`nxm_` prefix)
- **RLS:** PostgreSQL Row-Level Security on all user-scoped tables
- **Rate limiting:** Per-IP and per-user via slowapi + Redis
- **Brute-force protection:** Per-(email, IP) lockout with account-level escalation
- **Request guards:** Body size cap (5 MB), JSON depth/node limits, read-only kill switch
- **Circuit breaker:** OpenAI calls protected against cascading failures
- **Graceful shutdown:** In-flight request drain before process exit
- **Credential scanning:** CI blocks commits containing secrets
- **GDPR:** Data export + right-to-erasure endpoints with soft-delete

See [`SECURITY.md`](./SECURITY.md) for the vulnerability disclosure
policy and [`BACKEND_RISKS.md`](./BACKEND_RISKS.md) for the live
risk register.

---

## Testing

```bash
# Unit tests (demo mode, no external services)
pytest

# Integration tests (requires Postgres + Redis)
DEMO_MODE=false \
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/nexmem_test \
RUN_DB_TESTS=1 \
pytest -m integration

# Security audit
python tests/run_security_audit.py

# Credential scan
python scripts/scan_secrets.py
```

Test markers: `unit`, `integration`, `slow`. See
[`pytest.ini`](./pytest.ini) for defaults.

---

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for branching conventions,
secret handling rules, migration authoring guidelines, and the PR
checklist.

---

## License

Private — all rights reserved. Contact the maintainers for licensing
inquiries.
