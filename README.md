# NexMem - Decentralized AI Memory Layer

A persistent, cross-platform memory system for AI agents and LLMs, structured like human cognition into 4 memory types.

> **For investors / engineers reviewing the codebase:** read these in order.
> - [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) — feature-by-feature status (✅ / 🟡 / ❌).
> - [`PRODUCTION_READINESS_PLAN.md`](./PRODUCTION_READINESS_PLAN.md) — what is and is not ready.
> - [`BACKEND_RISKS.md`](./BACKEND_RISKS.md) — running risk register.
> - [`BACKEND_HARDENING_PHASE2.md`](./BACKEND_HARDENING_PHASE2.md) — what the in-flight backend hardening covers.
> - [`docs/SECRET_INCIDENT_RUNBOOK.md`](./docs/SECRET_INCIDENT_RUNBOOK.md) — credential-rotation playbook.

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
│  │ (RLS, FTS)      │ │(VECTOR(384))   │ │    (JSONB)   │ │  _graph  │  │
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

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/agents/{user_id}/episodes` | GET/POST | List/Create episodic memories |
| `/api/v1/agents/{user_id}/semantics` | GET/POST | List/Create semantic memories |
| `/api/v1/agents/{user_id}/semantic/search` | POST | Vector similarity search |
| `/api/v1/agents/{user_id}/procedural/settings` | GET/POST | Get/Upsert procedural memory |
| `/api/v1/agents/{user_id}/graph/nodes` | GET/POST | List/Create knowledge nodes |
| `/api/v1/agents/{user_id}/graph/edges` | GET/POST | List/Create knowledge edges |
| `/api/v1/agents/{user_id}/graph/path` | POST | Find path between nodes |
| `/api/v1/rag/chat` | POST | RAG-enhanced chat |
| `/api/v1/memory/stats/{user_id}` | GET | Memory statistics |

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
