# Project Overview: Decentralized AI Memory Layer

This document is a technical map of the repo for a second-pass reviewer. It is written for a senior engineer or AI system that needs to decide whether the project direction is coherent, implementable, and production-worthy.

---

## 1. What This Project Is

NexMem is a persistent AI memory layer for LLM agents. The backend stores conversation history, distilled knowledge, user preferences, and graph relationships so an agent can retrieve context across sessions instead of relying only on a short prompt window. The system is organized around four memory types: episodic memory for raw time-stamped interactions, semantic memory for vector-based recall, procedural memory for stable user preferences and workflows, and associative memory for graph-shaped relationships between entities and actions. A separate engram pipeline compresses raw episodic content into compact, reusable memory units.

The stack is centered on FastAPI, async SQLAlchemy, PostgreSQL with `pgvector`, Alembic migrations, spaCy, NetworkX, sentence-transformers, and OpenAI for retrieval-augmented generation. A Streamlit dashboard provides a live chat and memory feed on top of the same API. The repo also carries decentralized or multi-agent leaning features such as wallet-capable auth paths, API-key based access, and an `app_id` scoping model for multiple external agents or apps sharing one memory backend.

---

## 2. Architecture Overview

```text
External Agents / Apps
        |
        v
FastAPI API  <--------------------->  PostgreSQL + pgvector
   |   |   |                               |
   |   |   |                               +-- episodic_memory
   |   |   |                               +-- semantic_memory
   |   |   |                               +-- procedural_memory
   |   |   |                               +-- knowledge_nodes / knowledge_edges
   |   |   |                               +-- engrams
   |   |   |
   |   |   +-- RAG / retrieval / reranking / consolidation services
   |   |
   |   +------ Auth, rate limiting, health checks
   |
   +---------- Streamlit dashboard (live feed, chat, memory graph)
```

Major backend modules:

- `app/main.py`: FastAPI app bootstrap, middleware, router registration, startup/shutdown lifecycle, and top-level memory admin endpoints.
- `app/config.py`: Environment-backed settings. It drives DB URL, OpenAI settings, CORS, demo mode, and production validation.
- `app/database.py`: Async SQLAlchemy engine/session setup and the shared declarative base.
- `app/core/security.py`: JWT creation, password hashing, and API key generation/verification.
- `app/core/deps.py`: Authentication dependency that resolves a current user from JWT or API key.
- `app/core/rate_limit.py`: In-memory request throttling middleware.
- `app/models/*`: ORM models for users, API keys, episodic, semantic, procedural, graph, and engram data.
- `app/schemas/*`: Request/response models for the public API.
- `app/routers/*`: Endpoint layers for episodic, semantic, procedural, graph, memory, RAG, auth, health, app management, and memory stats.
- `app/services/embedder.py`: OpenAI embedding client and vector helper methods.
- `app/services/llm.py`: OpenAI chat completion wrapper for RAG responses.
- `app/services/engram_processor.py`: spaCy + sentence-transformers preprocessing and compression pipeline.
- `app/services/retriever.py`: Hybrid retrieval across vector, keyword, and graph sources.
- `app/services/reranker.py`: Cross-encoder reranking for combined retrieval results.
- `app/services/consolidation.py`: Background consolidation of older episodic memories into semantic and graph structures.
- `app/services/scheduler.py`: APScheduler integration for periodic consolidation jobs.
- `app/services/app_registry.py`: App-level key registration and scoping helpers.

Database structure:

- `users` and `api_keys` support auth and API-key issuance.
- `episodic_memory`, `semantic_memory`, and `procedural_memory` hold the main memory types.
- `knowledge_nodes` and `knowledge_edges` implement associative memory.
- `engrams` stores compressed distilled memory outputs.
- `memory_stats` and `recent_memories` are SQL views in the Supabase SQL migrations.

Background jobs:

- Consolidation is scheduled through APScheduler in production mode.
- Episodic cleanup is exposed as a database function and can be triggered manually through the API.

---

## 3. Memory Types and Data Model

### Episodic Memory

- Table: `episodic_memory`
- Key columns: `id`, `user_id`, `app_id`, `session_id`, `timestamp`, `content`, `metadata`, `tags`, `store_episodic`, `consolidated`, `consolidated_at`, `importance_score`, `created_at`
- Write path: `POST /api/v1/agents/{user_id}/episodes`, `POST /api/v1/memory/episode/write`, and demo helpers in `app/demo_db.py`
- Read path: `GET /api/v1/agents/{user_id}/episodes`, `GET /api/v1/agents/{user_id}/episodes/count`, `POST /api/v1/memory/context`, `GET /api/v1/memory/recent/{user_id}`

This is the raw conversation/event stream. It is the source material for consolidation and RAG.

### Semantic Memory

- Table: `semantic_memory`
- Key columns: `id`, `user_id`, `app_id`, `episodic_id`, `vector`, `embedding_model`, `summary`, `content_preview`, `metadata`, `index_semantic`, `created_at`
- Embedding dimension: `1536` in the main OpenAI-backed path, matching `text-embedding-3-small`; the engram pipeline also uses a separate local `384`-dimensional model for distilled engrams.
- Indexing: pgvector vector search with HNSW in migrations and vector similarity queries in the API
- Write path: `POST /api/v1/agents/{user_id}/semantics`, `POST /api/v1/memory/episode/write`, consolidation job
- Read path: `POST /api/v1/agents/{user_id}/semantic/search`, `GET /api/v1/agents/{user_id}/semantics`, `GET /api/v1/memory/context`

This is the meaning-based memory layer used for recall by similarity.

### Procedural Memory

- Table: `procedural_memory`
- Key columns: `id`, `user_id`, `app_id`, `settings`, `workflows`, `store_procedural`, `updated_at`, `created_at`
- Write path: `POST /api/v1/agents/{user_id}/procedural/settings`
- Read path: `GET /api/v1/agents/{user_id}/procedural/settings`, `POST /api/v1/memory/context`, `POST /api/v1/rag/chat`

This holds preferences, repeated behavior, and reusable workflow state.

### Associative Memory

- Tables: `knowledge_nodes`, `knowledge_edges`
- Key columns for nodes: `id`, `user_id`, `app_id`, `label`, `type`, `properties`, `store_associative`, `created_at`
- Key columns for edges: `id`, `user_id`, `app_id`, `from_node_id`, `to_node_id`, `relation`, `weight`, `metadata`, `created_at`
- Write path: `POST /api/v1/agents/{user_id}/graph/nodes`, `POST /api/v1/agents/{user_id}/graph/edges`, `POST /api/v1/memory/episode/write`, consolidation job
- Read path: `GET /api/v1/agents/{user_id}/graph/nodes`, `GET /api/v1/agents/{user_id}/graph/edges`, `POST /api/v1/agents/{user_id}/graph/path`, `GET /api/v1/agents/{user_id}/graph/stats`, `GET /api/v1/memory/context`, `POST /api/v1/rag/chat`

This is the graph layer for linking entities, actions, and related concepts across memories.

### Engrams

- Table: `engrams`
- Key columns: `id`, `user_id`, `engram_id`, `distilled_text`, `dense_embedding`, `actions`, `objects`, `entities`, `negated_actions`, `salience_scores`, `connections`, `original_length`, `compressed_length`, `compression_ratio`, `source_type`, `created_at`, `last_accessed_at`
- Write path: `POST /api/v1/memory/episode/write`, engram processor, and consolidation logic
- Read path: `GET /api/v1/memory/context`, `GET /api/v1/memory/stats/{user_id}`, direct SQL/service access in the backend

These are compact memory units derived from raw content. They are intended to become the distilled layer that sits between episodic raw logs and downstream retrieval.

---

## 4. Engram Processor / Preprocessing

The engram pipeline is implemented in `app/services/engram_processor.py`. It uses spaCy with `en_core_web_sm` for linguistic parsing, sentence-transformers with `all-MiniLM-L6-v2` for local dense embeddings, and NetworkX for in-memory co-occurrence graph structure. The pipeline extracts named entities, verbs/actions, negated actions, noun objects, and a token-level salience score. It also chunks long inputs before processing so the output stays compact and usable.

Compression is heuristic rather than lossless. The code generates a distilled text summary from truncated chunks and records `compression_ratio`, but I did not find a formal benchmark harness in the repo. The design intent is clearly to reduce large episodic input into a much smaller, reusable representation, and the repo comments describe that as roughly five-to-one compression. That number should be treated as a target, not a validated production metric.

Deduplication is not implemented as a single explicit database-level threshold in the current code. Instead, it is implicit across the pipeline through entity/action overlap, graph connections, and retrieval ranking. If you want a strict dedupe policy, this is the place to add one, because the repo currently favors permissive accumulation over aggressive suppression.

The co-occurrence graph is maintained in memory inside `EngramProcessor` using NetworkX, while the persistent graph is stored in PostgreSQL via `knowledge_nodes` and `knowledge_edges`. In other words, the local graph is a processing aid; the database graph is the durable associative memory.

---

## 5. Public API Endpoints

### Memory write and recall

- `POST /api/v1/memory/episode/write`
  - Purpose: single write path that stores an episodic record, generates a semantic vector, runs engram extraction, and seeds graph nodes
  - Auth: API key or JWT via the shared auth dependency
  - Memory types: writes episodic, semantic, engram, and graph
  - Status: stable core endpoint

- `POST /api/v1/memory/context`
  - Purpose: assembles a compact prompt context from engrams, semantic hits, recent episodes, preferences, and graph context
  - Auth: API key or JWT
  - Memory types: reads all major memory types
  - Status: stable core endpoint

- `POST /api/v1/rag/chat`
  - Purpose: retrieval-augmented chat endpoint that builds memory context and generates an LLM response
  - Auth: API key or JWT
  - Memory types: reads episodic, semantic, procedural, graph; writes a new episode after response
  - Status: stable, but dependent on OpenAI and retrieval health

### Auth and user lifecycle

- `POST /api/v1/auth/register`
  - Purpose: create a user account
  - Auth: none
  - Status: stable

- `POST /api/v1/auth/login`
  - Purpose: issue a JWT for email/password login
  - Auth: none
  - Status: stable

- `POST /api/v1/auth/api-keys`
  - Purpose: mint a new API key for the authenticated user
  - Auth: JWT or API key
  - Status: stable

- `GET /api/v1/auth/api-keys`
  - Purpose: list the user’s keys
  - Auth: JWT or API key
  - Status: stable

- `DELETE /api/v1/auth/api-keys/{key_id}`
  - Purpose: revoke a user key
  - Auth: JWT or API key
  - Status: stable

- `GET /api/v1/auth/me`
  - Purpose: current-user introspection
  - Auth: JWT or API key
  - Status: stable

### Per-memory routers

- `POST /api/v1/agents/{user_id}/episodes`
  - Purpose: write raw episodic memory
  - Auth: JWT or API key
  - Memory types: episodic
  - Status: stable

- `GET /api/v1/agents/{user_id}/episodes`
  - Purpose: list episodes
  - Auth: JWT or API key
  - Memory types: episodic
  - Status: stable

- `POST /api/v1/agents/{user_id}/semantics`
  - Purpose: create a semantic vector record
  - Auth: JWT or API key
  - Memory types: semantic
  - Status: stable

- `POST /api/v1/agents/{user_id}/semantic/search`
  - Purpose: search semantic memories by embedding similarity
  - Auth: JWT or API key
  - Memory types: semantic read
  - Status: stable

- `POST /api/v1/agents/{user_id}/procedural/settings`
  - Purpose: upsert the user’s procedural settings/workflows
  - Auth: JWT or API key
  - Memory types: procedural
  - Status: stable

- `GET /api/v1/agents/{user_id}/procedural/settings`
  - Purpose: fetch procedural memory
  - Auth: JWT or API key
  - Memory types: procedural read
  - Status: stable

- `POST /api/v1/agents/{user_id}/graph/nodes`
  - Purpose: create a graph node
  - Auth: JWT or API key
  - Memory types: graph
  - Status: stable

- `POST /api/v1/agents/{user_id}/graph/edges`
  - Purpose: create a graph edge
  - Auth: JWT or API key
  - Memory types: graph
  - Status: stable

- `POST /api/v1/agents/{user_id}/graph/path`
  - Purpose: find a path between graph nodes
  - Auth: JWT or API key
  - Memory types: graph read
  - Status: experimental

### Admin and support

- `GET /health/live`
  - Purpose: liveness probe
  - Auth: none
  - Status: stable

- `GET /health/ready`
  - Purpose: readiness probe across DB and embedding service
  - Auth: none
  - Status: stable

- `POST /api/v1/memory/cleanup`
  - Purpose: trigger episodic cleanup
  - Auth: JWT or API key
  - Memory types: episodic maintenance
  - Status: operational/admin

- `GET /api/v1/memory/stats/{user_id}`
  - Purpose: fetch per-user memory counts
  - Auth: JWT or API key
  - Memory types: read-only summary
  - Status: stable

- `GET /api/v1/memory/recent/{user_id}`
  - Purpose: fetch a recent feed across memory types
  - Auth: JWT or API key
  - Memory types: read-only summary
  - Status: stable

- `POST /api/v1/demo/reset`
  - Purpose: reset demo-mode in-memory data
  - Auth: JWT or API key
  - Memory types: demo only
  - Status: demo-only

---

## 6. Auth, Security, and MultiTenancy

The user model is in `app/models/user.py`. It supports `email`, `wallet_address`, `hashed_password`, `is_active`, and `created_at`. API keys are stored separately in `api_keys` with a SHA-256 hash, a user reference, a name, scopes, and last-used tracking.

`app/core/deps.py` is the key gatekeeper. It reads the `Authorization` header and supports either `Bearer <JWT>` or `ApiKey <raw_key>`, then resolves the current user from the database. In demo mode, the auth dependency returns a synthetic user and bypasses normal credential checks.

User and app scoping is enforced by propagating `user_id` through almost every query. Path-level `user_id` parameters are also validated against the authenticated identity in the routers, and many queries also honor `app_id` for multi-app isolation. This is the right direction, but the real security boundary still needs database-level row-level security if you want defense in depth.

Rate limiting is implemented in-process with `app/core/rate_limit.py`. That is fine for a single-process development setup, but it is not a strong production limiter because it does not coordinate across workers or multiple instances.

CORS is configured from settings in `app/main.py`. Production should keep this constrained to the frontend origin(s), not wildcarded.

Security TODOs and risks:

- `DEMO_MODE` must stay off in production.
- JWTs would benefit from stronger claims and refresh/revocation handling.
- API key scope enforcement exists conceptually but is still simpler than a full policy engine.
- Sensitive metadata is not encrypted at rest at the application layer.
- In-process rate limiting should be replaced with Redis or edge-level throttling for production.

---

## 7. Deployment, Environments, and Tooling

Local development is supported with Docker Compose and `.env`-style configuration. The repo contains `docker-compose.yml` for local development and `docker-compose.prod.yml` for a production-flavored stack. The backend container runs FastAPI, the frontend container runs Streamlit, and Postgres with `pgvector` is available for local development.

The production path is Docker-based and intended to work with a hosted Postgres service such as Supabase or another managed PostgreSQL provider. `alembic` is used for schema migration management, and the repo now has an Alembic head that matches the current code path.

The frontend lives in `frontend/app.py`. It is a Streamlit dashboard with three main panes: memory graph, memory-enhanced chat, and a live memory feed. It talks to the backend through the public HTTP API and supports either JWT or API key auth tokens.

External services used in the current code:

- OpenAI for embeddings and chat completions
- PostgreSQL with `pgvector`
- spaCy model `en_core_web_sm`
- sentence-transformers model `all-MiniLM-L6-v2`

CI/test setup in the repo is lightweight:

- `pytest` is present and now configured through `pytest.ini`
- `tests/` contains API and processor tests
- Integration tests are gated behind `RUN_DB_TESTS=1`
- ML-heavy tests are gated behind `RUN_ML_TESTS=1`

Current operational status:

- The backend is runnable locally.
- The production configuration path is now stricter about unsafe defaults.
- A full end-to-end deployment still depends on real environment variables, a reachable database, and OpenAI access.

---

## 8. Current Status and Roadmap

### Done

- FastAPI backend with modular routers for episodic, semantic, procedural, graph, RAG, auth, health, and app management.
- Async SQLAlchemy database layer and Alembic migration chain.
- PostgreSQL + `pgvector` support for semantic retrieval.
- User auth with JWT and API keys.
- Streamlit dashboard for chat, graph, and live feed.
- Engram processor using spaCy, sentence-transformers, and NetworkX.
- Consolidation service and background scheduler scaffold.
- Hybrid retrieval and reranking services.
- Production hardening work: config validation, Docker non-root runtime, health checks, lazy ML loading, and test gating.

### In Progress

- Multi-app isolation is implemented in code paths, but it still needs more end-to-end validation.
- Graph and app-scoping behavior should be exercised against a real database before calling it final.
- The in-process rate limiter is still a development-grade control.
- The engram deduplication policy is still implicit rather than a formal threshold-driven rule.

### Next 7 Days

- Validate the full backend against a real Supabase/Postgres instance with `RUN_DB_TESTS=1`.
- Add/repair database-level tests for auth, scoping, and memory writes.
- Decide whether `app_id` should be a first-class `apps` table rather than a scope token embedded in API-key scopes.
- Tighten the retrieval pipeline with formal reranking and relevance metrics.
- Add explicit benchmark numbers for engram compression and retrieval latency.
- Review row-level security and harden tenant isolation at the database layer.

### Known Issues / Questions

- Should procedural memory remain one row per user, or become one row per user per app?
- Should app registration be a true app model instead of using API keys plus scope strings?
- Do you want deterministic deduplication in engram generation, or is best-effort accumulation enough?
- Should the background consolidation job run continuously in-process, or be offloaded to a worker queue?
- How strict should production readiness be for demo-mode fallback behavior?

