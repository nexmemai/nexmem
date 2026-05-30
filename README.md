# NexMem - Decentralized AI Memory Layer

A persistent, cross-platform memory system for AI agents and LLMs, structured like human cognition into 4 memory types.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Streamlit Dashboard                              │
│  ┌──────────────┐  ┌─────────────────────┐  ┌──────────────────────┐  │
│  │ Memory Graph  │  │   Memory Chat       │  │  Live Memory Feed    │  │
│  │ (Nodes+Edges) │  │   (RAG-enabled)     │  │  (Real-time updates) │  │
│  └──────────────┘  └─────────────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         FastAPI Backend                                  │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────┐ ┌────────────────┐  │
│  │ Episodic │ │ Semantic │ │Procedural │ │Graph │ │   RAG Engine   │  │
│  │  Router  │ │  Router  │ │  Router   │ │Router│ │  (GPT-4o)      │  │
│  └──────────┘ └──────────┘ └───────────┘ └──────┘ └────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    PostgreSQL + pgvector (Supabase)                      │
│  ┌─────────────────┐ ┌────────────────┐ ┌──────────────┐ ┌──────────┐  │
│  │ episodic_memory │ │semantic_memory │ │procedural_mem │ │knowledge │  │
│  │ (hypertable)    │ │(VECTOR(384))   │ │    (JSONB)   │ │  _graph  │  │
│  └─────────────────┘ └────────────────┘ └──────────────┘ └──────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Memory Types

| Type | Description | Storage | Decay |
|------|-------------|---------|-------|
 | 🧠 **Episodic** | Time-stamped conversation history | PostgreSQL hypertable | 30 days (configurable) |
| 🔍 **Semantic** | Vector embeddings for meaning search | pgvector (384-dim) | Never |
| ⚙️ **Procedural** | User preferences, settings, workflows | JSONB | Never |
| 🕸️ **Associative** | Knowledge graph relationships | Nodes + Edges | Never |

## Quick Start

### Option 1: Local Development (Docker Compose)

```bash
# Clone the repository
git clone <repo-url>
cd memorylayer

# Set your OpenAI API key
export OPENAI_API_KEY=sk-...

# Start all services
docker-compose up --build

# Access the dashboard
open http://localhost:8501
```

### Option 2: Supabase + Local Services

1. **Create Supabase Project**
   - Go to https://app.supabase.com
   - Create a new project
   - Enable pgvector extension: `CREATE EXTENSION IF NOT EXISTS "vector";`

2. **Run Migration**
   - Open Supabase SQL Editor
   - Copy and run `supabase/migrations/001_initial_schema.sql`

3. **Configure Backend**
   ```bash
   cd backend
   cp .env.example .env
   # Edit .env with your DATABASE_URL and OPENAI_API_KEY
   ```

4. **Start Backend**
   ```bash
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```

5. **Start Frontend**
   ```bash
   cd frontend
   pip install -r requirements.txt
   # Update .streamlit/secrets.toml with your backend URL
   streamlit run app.py
   ```

### SDK Quickstarts (Python and JavaScript)

Once the backend is running locally, the fastest way to drive it from a
client is via one of the SDK quickstarts. Both register a throwaway
demo user, mint an `nxm_`-prefixed API key, and exercise `remember` +
`recall` end-to-end:

- Python: [`examples/python_quickstart.py`](./examples/python_quickstart.py)
- JavaScript / TypeScript: [`examples/javascript_quickstart.mjs`](./examples/javascript_quickstart.mjs)
- Prereqs and per-language commands: [`examples/README.md`](./examples/README.md)

The SDK source lives under [`nexmem-py/`](./nexmem-py/README.md) and
[`nexmem-js/`](./nexmem-js/README.md). Neither package is published to
PyPI / npm yet; install both from this repository for local use.

## Security / Secrets

- **Git history rewrite (complete):** the Phase 1 incident — a leaked Supabase
  database password, project ref, and a GitHub PAT — was purged from the
  entire git history with `git-filter-repo` and force-pushed across all
  branches and tags. No real secrets remain in remote history. See
  [`HISTORY_REWRITE_COMPLETE.md`](./HISTORY_REWRITE_COMPLETE.md). Collaborators
  must delete old clones and re-clone.
- **Secret scanner:** [`scripts/scan_secrets.py`](./scripts/scan_secrets.py)
  scans every tracked file for credential patterns (Postgres URLs with
  passwords, Supabase hostnames, OpenAI/GitHub/AWS keys, JWTs) and enforces a
  **SHA-256 hash-based tripwire** for the known rotated incident value — the
  cleartext is never stored, but a re-leak of the same value still fails the
  scan. Run it locally with:

  ```bash
  python scripts/scan_secrets.py
  # exits 0 and prints "clean" when no secrets are found; exits 1 on a hit
  ```

  CI runs the same scanner; the test suite pins its behaviour in
  `tests/test_secret_scan.py`.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/memory/episode/write` | POST | Unified write for episodic/semantic/procedural/graph |
| `/api/v1/memory/context` | POST | Unified context assembly |
| `/api/v1/rag/chat` | POST | RAG-enhanced chat |
| `/api/v1/memory/user/{id}/export` | GET | Streaming GDPR export |
| `/api/v1/memory/user/{id}/all` | DELETE | Atomic GDPR soft-delete |
| `/api/v1/auth/register` | POST | User registration |
| `/api/v1/auth/login` | POST | User login |
| `/api/v1/auth/api-keys` | POST | Mint API key |

## Project Structure

```
nexmem/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI application
│   │   ├── config.py         # Settings from env vars
│   │   ├── database.py       # Async SQLAlchemy connection
│   │   ├── models/
│   │   │   └── memory.py     # SQLAlchemy ORM models
│   │   ├── schemas/
│   │   │   └── memory.py     # Pydantic request/response schemas
│   │   ├── routers/
│   │   │   ├── episodic.py   # Episodic memory endpoints
│   │   │   ├── semantic.py   # Semantic search endpoints
│   │   │   ├── procedural.py # Procedural memory endpoints
│   │   │   ├── graph.py      # Knowledge graph endpoints
│   │   │   └── rag.py        # RAG chat endpoint
│   │   └── services/
│   │       ├── embedder.py   # OpenAI embedding service
│   │       └── llm.py        # LLM service (GPT-4o)
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
├── frontend/
│   ├── app.py                # Streamlit dashboard
│   ├── requirements.txt
│   ├── .env.example
│   ├── .streamlit/
│   │   └── secrets.toml
│   └── Dockerfile
├── supabase/
│   └── migrations/
│       └── 001_initial_schema.sql
├── docker-compose.yml
└── README.md
```

## Environment Variables

### Backend (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | - | PostgreSQL connection string |
| `OPENAI_API_KEY` | - | OpenAI API key |
 | `OPENAI_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Embedding model |
| `OPENAI_LLM_MODEL` | `gpt-4o` | LLM for RAG responses |
| `MEMORY_DECAY_DAYS` | `30` | Days before episodic cleanup |
| `SEMANTIC_TOP_K` | `5` | Default search results |
| `DEBUG` | `false` | Enable debug mode |

## MVP Success Criteria

- [x] End-to-end retrieval loop works for a single agent
- [x] Semantic search returns relevant results
- [x] Episodic history informs the LLM system prompt
- [x] Data retention and privacy flags behave as specified
