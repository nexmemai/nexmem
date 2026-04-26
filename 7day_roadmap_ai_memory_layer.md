# 7-Day Build Roadmap — Decentralized AI Memory Layer
### Starting from your current state → Production-Ready MVP

---

## Current State Snapshot

Before the sprint begins, you have:

- FastAPI backend (`app/main.py`, `app/config.py`, `app/database.py`)
- ORM models for 4 memory types: episodic, semantic, procedural, graph
- Pydantic schemas + 5 API route modules
- LLM + embedding services
- Streamlit dashboard (graph viz, RAG chat, live feed)
- PostgreSQL + pgvector with IVFFlat index
- Seed data for `demo_user`
- Docker Compose stack
- MCP0 Engram Processor (spaCy + NetworkX, v0 state)

**What this roadmap builds on top of that:** auth, migrations, engram refinement,
production hardening, unified context API, deployment, and a polished demo.

---

## Day 1 — Foundation Cleanup & Project Structure

**Goal:** Clean structure, no dead files, config properly separated, local stack stable.

### Morning (3–4 hours)

**Reorganize folder structure to this exact layout:**

```
project-root/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── deps.py          ← NEW: all FastAPI dependencies (get_db, get_current_user)
│   │   ├── security.py      ← NEW: password hashing, JWT, API key generation
│   │   └── logging.py       ← NEW: structured logging setup
│   ├── models/
│   │   ├── __init__.py
│   │   ├── memory.py        ← existing
│   │   ├── user.py          ← NEW
│   │   └── engram.py        ← NEW
│   ├── schemas/
│   │   ├── memory.py        ← existing
│   │   ├── user.py          ← NEW
│   │   └── engram.py        ← NEW
│   ├── routers/
│   │   ├── episodic.py      ← existing
│   │   ├── semantic.py      ← existing
│   │   ├── procedural.py    ← existing
│   │   ├── graph.py         ← existing
│   │   ├── rag.py           ← existing
│   │   ├── auth.py          ← NEW
│   │   ├── memory.py        ← NEW: unified /memory/context
│   │   └── health.py        ← NEW
│   └── services/
│       ├── llm.py           ← existing
│       ├── embedding.py     ← existing
│       └── engram_processor.py ← UPGRADE from MCP0 script
├── alembic/
│   ├── env.py
│   └── versions/
├── scripts/
│   ├── seed_demo.py         ← move seed data here
│   └── check_health.py
├── tests/
│   ├── conftest.py
│   └── test_memory.py
├── docker-compose.yml
├── docker-compose.prod.yml  ← NEW
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt     ← NEW
├── .env.example
├── .env.local               ← gitignored
├── .env.production          ← gitignored
├── alembic.ini
└── README.md
```

### Afternoon (2–3 hours)

**Split environment files:**

`.env.local`:
```
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_memory_dev
QDRANT_URL=http://localhost:6333
OPENAI_API_KEY=sk-...
SECRET_KEY=local-dev-secret-change-this
ENVIRONMENT=development
```

`.env.production`:
```
DATABASE_URL=postgresql+asyncpg://<render-url>
OPENAI_API_KEY=sk-...
SECRET_KEY=<long-random-string>
ENVIRONMENT=production
ALLOWED_ORIGINS=https://yourdomain.com
```

**Pin requirements.txt:**
```
fastapi==0.115.0
uvicorn[standard]==0.30.0
sqlalchemy[asyncio]==2.0.30
asyncpg==0.29.0
alembic==1.13.2
pydantic==2.7.1
pydantic-settings==2.3.0
pgvector==0.3.0
spacy==3.7.4
networkx==3.3
sentence-transformers==3.0.1
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
httpx==0.27.0
```

**requirements-dev.txt:**
```
pytest==8.2.0
pytest-asyncio==0.23.7
httpx==0.27.0
black==24.4.2
ruff==0.4.7
```

**Deliverables for Day 1:**
- [ ] Folder structure matches layout above
- [ ] `docker-compose up` still works
- [ ] `.env.example` has all variables listed
- [ ] `requirements.txt` is pinned
- [ ] No duplicate launch scripts (remove `start_servers.py`, `start_backend.bat`, `start_frontend.bat`)

---

## Day 2 — Alembic Migrations + User Model

**Goal:** Replace ad hoc schema creation with proper migrations. Add user/auth tables.

### Morning (3 hours) — Alembic Setup

**Install and initialize Alembic with async support:**
```bash
pip install alembic asyncpg
alembic init -t async alembic
```

**Edit `alembic/env.py`** to use your async engine and read `DATABASE_URL`:
```python
from app.config import settings
from app.database import Base
from app.models import user, memory, engram  # import all models so Alembic sees them

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata
```

**Delete existing tables** (you're migrating from scratch):
```bash
docker-compose exec postgres psql -U user -d ai_memory_dev -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

**Create first migration:**
```bash
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head
```

### Afternoon (3 hours) — User + API Key Models

**Create `app/models/user.py`:**
```python
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email         = Column(String, unique=True, nullable=True, index=True)
    wallet_address = Column(String, unique=True, nullable=True, index=True)
    hashed_password = Column(String, nullable=True)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

class APIKey(Base):
    __tablename__ = "api_keys"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id     = Column(UUID(as_uuid=True), nullable=False, index=True)
    key_hash    = Column(String, unique=True, nullable=False)
    name        = Column(String, nullable=False)   # e.g., "Telegram Bot", "VS Code"
    scopes      = Column(String, default="read,write")
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
```

**Add `user_id` to ALL existing memory models:**
```python
# Add this to episodic_memory, semantic_memory, procedural_memory,
# knowledge_nodes, knowledge_edges tables:
user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
app_id  = Column(UUID(as_uuid=True), nullable=True, index=True)
```

**Create migration:**
```bash
alembic revision --autogenerate -m "add_users_and_user_id_to_memories"
alembic upgrade head
```

**Create `scripts/seed_demo.py`:**
```python
# Standalone script — not part of app startup
# Usage: python scripts/seed_demo.py
# Creates demo_user with known API key + seeds all 4 memory types
```

**Deliverables for Day 2:**
- [ ] `alembic upgrade head` creates all tables cleanly
- [ ] App startup does NOT call `create_all()`
- [ ] `users` and `api_keys` tables exist in DB
- [ ] All memory tables have `user_id` column
- [ ] `seed_demo.py` runs without errors

---

## Day 3 — Auth Layer (API Key + JWT)

**Goal:** Every memory endpoint requires auth. No data leaks across users.

### Morning (3 hours) — Auth Core

**Create `app/core/security.py`:**
```python
import secrets
import hashlib
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"])

def generate_api_key() -> tuple[str, str]:
    """Returns (raw_key, hashed_key). Store only hash."""
    raw = f"mem_{secrets.token_urlsafe(32)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed

def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    return hashlib.sha256(raw_key.encode()).hexdigest() == stored_hash

def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
```

**Create `app/core/deps.py`:**
```python
from fastapi import Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.core.security import verify_api_key, decode_token
from app.models.user import User, APIKey

async def get_current_user(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db)
) -> User:
    # Support both:
    # Authorization: Bearer <jwt_token>
    # Authorization: ApiKey <api_key>
    scheme, _, token = authorization.partition(" ")

    if scheme.lower() == "bearer":
        payload = decode_token(token)
        user = await db.get(User, payload["sub"])

    elif scheme.lower() == "apikey":
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        api_key = await db.execute(
            select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active == True)
        )
        api_key = api_key.scalar_one_or_none()
        if not api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        user = await db.get(User, api_key.user_id)

    else:
        raise HTTPException(status_code=401, detail="Invalid auth scheme")

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user
```

### Afternoon (3 hours) — Auth Router + Protect All Endpoints

**Create `app/routers/auth.py`:**

Endpoints to implement:
- `POST /auth/register` — email + password or wallet address
- `POST /auth/login` — returns JWT
- `POST /auth/api-keys` — create named API key (returns raw key ONCE)
- `GET /auth/api-keys` — list user's API keys
- `DELETE /auth/api-keys/{key_id}` — revoke key

**Update every existing router** to use the auth dependency:
```python
# Before (unprotected):
@router.get("/episodes")
async def get_episodes(db: AsyncSession = Depends(get_db)):
    return await db.execute(select(Episode)).scalars().all()

# After (protected + scoped):
@router.get("/episodes")
async def get_episodes(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    return await db.execute(
        select(Episode).where(Episode.user_id == user.id)
    ).scalars().all()
```

**Rule: every SELECT must filter by `user_id`. No exceptions.**

**Deliverables for Day 3:**
- [ ] Register + login endpoints work
- [ ] API key creation returns raw key exactly once
- [ ] Every memory endpoint returns 401 without valid auth
- [ ] No memory endpoint can return another user's data
- [ ] Demo user has a working API key in `seed_demo.py`

---

## Day 4 — Engram Processor Upgrade (All 6 Fixes)

**Goal:** Production-grade engram preprocessing with real embeddings, persistence,
weighted scoring, async safety, deduplication, and negation.

### Morning (3 hours) — Core Upgrades

**Upgrade `app/services/engram_processor.py`** with all 6 fixes in this order:

**Fix 1 — Async wrapper (critical, do first):**
```python
import asyncio
from functools import partial

async def process_async(self, text: str, user_id: str) -> Dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(self._process_sync, text, user_id))
```

**Fix 2 — Real dense embeddings:**
```python
from sentence_transformers import SentenceTransformer
_embed_model = SentenceTransformer("all-MiniLM-L6-v2")  # local, free, fast

def _get_embedding(self, text: str) -> List[float]:
    return _embed_model.encode(text, normalize_embeddings=True).tolist()
```

**Fix 3 — Weighted co-occurrence scoring:**
```python
weight = (
    len(set(actions) & set(existing["actions"])) * 1.0 +
    len(set(objects) & set(existing["objects"])) * 1.5 +
    len(set(entities) & set(existing["entities"])) * 2.5
)
self.graph.add_edge(engram_id, eid, weight=round(weight, 2))
```

**Fix 4 — Negation detection:**
```python
for token in doc:
    if token.pos_ == "VERB":
        has_neg = any(child.dep_ == "neg" for child in token.children)
        key = f"NOT_{token.lemma_}" if has_neg else token.lemma_
        actions.append(key)
```

**Fix 5 — Salience scoring:**
```python
def _score_salience(self, token: str, doc) -> float:
    score = 1.0
    if any(ent.text == token for ent in doc.ents): score += 2.0
    if any(ch.isdigit() for ch in token): score += 1.5
    if any(t.dep_ in ("nsubj","dobj") and t.text == token for t in doc): score += 1.0
    if len(token) <= 3: score -= 0.5
    return max(score, 0.1)
```

**Fix 6 — Chunking for long input:**
```python
def _chunk_text(self, text: str, max_tokens: int = 200) -> List[str]:
    words = text.split()
    step = max_tokens - 20
    return [" ".join(words[i:i+max_tokens]) for i in range(0, len(words), step)]
```

### Afternoon (3 hours) — Engram DB Persistence + Deduplication

**Create `app/models/engram.py`:**
```python
class Engram(Base):
    __tablename__ = "engrams"
    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id           = Column(UUID(as_uuid=True), nullable=False, index=True)
    engram_id         = Column(String(12), nullable=False, index=True)
    distilled_text    = Column(String, nullable=False)
    dense_embedding   = Column(Vector(384))  # MiniLM output
    actions           = Column(JSON, default=list)
    objects           = Column(JSON, default=list)
    entities          = Column(JSON, default=list)
    negated_actions   = Column(JSON, default=list)
    salience_scores   = Column(JSON, default=dict)
    original_length   = Column(Integer)
    compressed_length = Column(Integer)
    compression_ratio = Column(Float)
    connections       = Column(JSON, default=list)
    source_type       = Column(String)
    created_at        = Column(DateTime, default=datetime.utcnow, index=True)
    last_accessed_at  = Column(DateTime, nullable=True)
```

**Add deduplication check before insert:**
```python
async def is_duplicate(embedding: List[float], user_id: str, db, threshold=0.95) -> bool:
    result = await db.execute(
        text("""
            SELECT engram_id, 1 - (dense_embedding <=> :vec::vector) as sim
            FROM engrams WHERE user_id = :uid
            ORDER BY dense_embedding <=> :vec::vector LIMIT 1
        """),
        {"vec": str(embedding), "uid": str(user_id)}
    )
    row = result.fetchone()
    return bool(row and row.sim >= threshold)
```

**Add temporal decay utility:**
```python
import math
from datetime import datetime, timezone

def decay_score(created_at: datetime, half_life_days: float = 30.0) -> float:
    age = (datetime.now(timezone.utc) - created_at.replace(tzinfo=timezone.utc)).days
    return math.exp(-0.693 * age / half_life_days)
```

**Create Alembic migration:**
```bash
alembic revision --autogenerate -m "add_engrams_table"
alembic upgrade head
```

**Deliverables for Day 4:**
- [ ] EngramProcessor runs async without blocking event loop
- [ ] Real 384D dense embeddings stored in `engrams.dense_embedding`
- [ ] Deduplication skips near-identical engrams (>0.95 similarity)
- [ ] Co-occurrence edges are weighted (not all 1.00)
- [ ] Negation produces `NOT_verb` entries
- [ ] Engram graph persists to `knowledge_nodes` + `knowledge_edges` on every write

---

## Day 5 — Unified Memory Context API + Health Endpoints

**Goal:** One powerful endpoint for agents to call. Observability for production.

### Morning (3 hours) — /memory/context Endpoint

**Create `app/routers/memory.py`** — the most important endpoint in the system:

```python
@router.post("/memory/context")
async def get_memory_context(
    body: ContextRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Master context assembly endpoint.
    Call this before every LLM generation.
    Returns compressed, ranked, multi-source context.
    """
    # 1. Run engram compression on the query
    engram = await engram_processor.process_async(body.query, str(user.id))
    engram_context = engram_processor.get_compressed_context(body.query, str(user.id))

    # 2. Semantic vector search (pgvector)
    query_embedding = await embedding_service.embed(body.query)
    semantic_hits = await semantic_service.search(
        embedding=query_embedding,
        user_id=user.id,
        top_k=body.semantic_top_k or 5
    )

    # 3. Recent episodic memories with decay scoring
    recent_episodes = await episodic_service.get_recent(
        user_id=user.id,
        limit=body.episodic_limit or 5
    )
    # Apply decay to each episode
    for ep in recent_episodes:
        ep["relevance_score"] = decay_score(ep["created_at"])

    # 4. Procedural preferences (stable facts)
    preferences = await procedural_service.get_profile(user.id)

    # 5. Graph relationships for entities in query
    graph_context = await graph_service.get_related(
        entities=engram.get("entities", []),
        user_id=user.id,
        depth=2
    )

    # 6. Assemble final context string for LLM injection
    assembled = assemble_context(
        engram_context=engram_context,
        semantic_hits=semantic_hits,
        recent_episodes=recent_episodes,
        preferences=preferences,
        graph_context=graph_context,
        max_tokens=body.max_tokens or 1200
    )

    return {
        "assembled_context": assembled,      # inject this into LLM system prompt
        "engram_context": engram_context,
        "semantic_hits": semantic_hits,
        "recent_episodes": recent_episodes,
        "preferences": preferences,
        "graph_context": graph_context,
        "metadata": {
            "engram_id": engram["engram_id"],
            "compression_ratio": engram["compression_ratio"],
            "sources_used": 5,
            "total_tokens": len(assembled.split())
        }
    }
```

**ContextRequest schema:**
```python
class ContextRequest(BaseModel):
    query: str
    semantic_top_k: int = 5
    episodic_limit: int = 5
    max_tokens: int = 1200
    filters: Optional[Dict] = None
```

**Add `POST /memory/episode/write`** — unified write endpoint that:
1. Saves to episodic table
2. Generates embedding → semantic table
3. Runs engram processor → engrams table
4. Extracts entities → graph table
5. Extracts preferences → procedural table
6. Returns summary of what was stored

### Afternoon (2 hours) — Health Endpoints

**Create `app/routers/health.py`:**
```python
@router.get("/health/live")
async def liveness():
    """Fast check — is the process alive?"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    """Thorough check — are all dependencies reachable?"""
    checks = {}

    # Check DB
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {str(e)}"

    # Check embedding model
    try:
        test = await embedding_service.embed("health check")
        checks["embedding_service"] = "ok" if len(test) > 0 else "error"
    except:
        checks["embedding_service"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
            "version": "0.1.0",
            "timestamp": datetime.utcnow().isoformat()
        }
    )
```

**Add structured logging middleware in `app/main.py`:**
```python
import time
import logging
import uuid

logger = logging.getLogger("ai_memory")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000)
    logger.info(f"[{request_id}] {request.method} {request.url.path} "
                f"→ {response.status_code} ({duration_ms}ms)")
    return response
```

**Deliverables for Day 5:**
- [ ] `POST /memory/context` returns all 5 memory sources in one call
- [ ] `POST /memory/episode/write` writes to all 5 stores simultaneously
- [ ] `GET /health/live` responds in < 50ms
- [ ] `GET /health/ready` returns 503 when DB is down
- [ ] Every request logs: method, path, status, duration

---

## Day 6 — pgvector Tuning + API Polish + Tests

**Goal:** Search quality improvements. Rate limiting. Pagination. Basic tests.

### Morning (3 hours) — Vector Search Improvements

**Evaluate whether to switch IVFFlat → HNSW:**

- IVFFlat: better for bulk-load-then-query patterns
- HNSW: better for continuous insertion (your use case — memory grows over time)

**Add HNSW index via migration:**
```sql
-- Only create after you have >1000 vectors
-- Drop IVFFlat first, add HNSW:
DROP INDEX IF EXISTS semantic_memory_embedding_idx;
CREATE INDEX ON semantic_memory
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Create migration:**
```bash
alembic revision -m "switch_to_hnsw_index"
```

**Add filters to all semantic searches:**
```python
async def search(
    embedding: List[float],
    user_id: UUID,
    top_k: int = 5,
    source_app: Optional[str] = None,
    after_date: Optional[datetime] = None,
    min_relevance: float = 0.5
) -> List[Dict]:
    query = """
        SELECT id, content, metadata,
               1 - (embedding <=> :vec::vector) as similarity,
               created_at
        FROM semantic_memory
        WHERE user_id = :uid
        AND 1 - (embedding <=> :vec::vector) >= :min_rel
        {date_filter}
        ORDER BY embedding <=> :vec::vector
        LIMIT :k
    """
```

**Add pagination to all list endpoints:**
```python
class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 20  # max 100

# In each router:
@router.get("/episodes")
async def get_episodes(
    pagination: PaginationParams = Depends(),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    offset = (pagination.page - 1) * pagination.page_size
    ...
```

**Add rate limiting:**
```python
# In requirements.txt:
slowapi==0.1.9

# In main.py:
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/memory/context")
@limiter.limit("60/minute")
async def get_memory_context(...):
```

### Afternoon (2 hours) — Basic Tests

**Create `tests/conftest.py`:**
```python
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.fixture
async def client():
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c

@pytest.fixture
async def auth_headers(client):
    # Register + login demo user, return headers
    ...
```

**Create `tests/test_memory.py`** with these test cases:
1. `test_health_live` — GET /health/live returns 200
2. `test_health_ready` — GET /health/ready returns 200
3. `test_write_episode_requires_auth` — returns 401 without token
4. `test_write_and_retrieve_episode` — write episode, retrieve it
5. `test_context_endpoint_returns_all_sources` — /memory/context returns all 5 keys
6. `test_user_isolation` — user A cannot see user B's memories
7. `test_engram_compression` — compression ratio > 0

**Run tests:**
```bash
pytest tests/ -v --asyncio-mode=auto
```

**Deliverables for Day 6:**
- [ ] HNSW migration prepared (run when data > 1000 rows)
- [ ] All search endpoints accept filters + pagination
- [ ] Rate limiting active (60 req/min per IP)
- [ ] 7 tests pass
- [ ] `test_user_isolation` passes (most important security test)

---

## Day 7 — Deploy + Demo Polish

**Goal:** Live public URL. Polished demo flow. Investor-ready walkthrough.

### Morning (3 hours) — Deploy to Render

**Step 1 — Prepare for deployment:**

Create `docker-compose.prod.yml`:
```yaml
version: "3.9"
services:
  api:
    build: .
    ports: ["8000:8000"]
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - SECRET_KEY=${SECRET_KEY}
      - ENVIRONMENT=production
    command: >
      sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"
```

**Step 2 — Render setup:**
1. Go to render.com → sign in with GitHub
2. New → Web Service → connect your repo
3. Choose "Docker" runtime
4. Set environment variables:
   - `DATABASE_URL` from Render Postgres
   - `OPENAI_API_KEY`
   - `SECRET_KEY` (generate: `python -c "import secrets; print(secrets.token_hex(32))"`)
   - `ENVIRONMENT=production`
5. New → PostgreSQL → smallest tier → copy Internal Connection String
6. Set Health Check Path: `/health/live`
7. Click Deploy

**Step 3 — Post-deploy checks:**
```bash
# From your laptop:
curl https://your-app.onrender.com/health/ready
# Should return: {"status": "ready", "checks": {"database": "ok", ...}}

# Run seed data against production:
DATABASE_URL=<prod_url> python scripts/seed_demo.py
```

### Afternoon (3 hours) — Streamlit Dashboard Polish

**Update your Streamlit `app.py`** to use the live API URL instead of localhost.

**Add these panels to the dashboard:**

1. **Engram Compression Panel:**
   - Text input → paste any message
   - Shows: Engram ID, compression ratio, extracted entities
   - Shows: co-occurrence connections as a mini-graph
   - Live output matching your MCP0 screenshot format

2. **Memory Timeline:**
   - Chronological view of all episodic memories
   - Color-coded by source app
   - Decay score shown as a fade effect on older items

3. **Context Preview Panel:**
   - Text input: "What would an agent know about me if I asked: [query]?"
   - Calls `/memory/context` and shows assembled context
   - Shows token count before and after compression

4. **API Key Manager:**
   - Create/revoke API keys
   - Shows last used timestamp

**Deploy Streamlit:**
```bash
# Add to requirements.txt:
streamlit==1.35.0

# Render: New → Web Service → same repo
# Start command: streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

### End of Day 7 — Demo Flow Script

Prepare this exact walkthrough for demos/investors:

```
1. Open Streamlit dashboard URL

2. Paste this message in Engram Panel:
   "Analyze our Q3 2025 sales pipeline opportunity — deal value is 75000 USD"
   → Show 78% compression, co-occurrence graph

3. Open Memory Chat:
   Ask: "What do I prefer for technical projects?"
   → Memory pulls procedural preferences

4. Switch to a different app (e.g., Telegram bot demo):
   → Same wallet/user ID
   → Bot already knows preferences from step 3
   → This is the "wow moment" — persistent memory across apps

5. Show /memory/context JSON in API tab:
   → assembled_context field
   → 5 sources combined
   → token count: 58 raw → 13 compressed

6. Show API key panel:
   → Developer flow: get key → add to any AI app → memory works
```

**Deliverables for Day 7:**
- [ ] Backend live at `https://your-app.onrender.com`
- [ ] Streamlit dashboard live
- [ ] `GET /health/ready` returns 200 in production
- [ ] Seed demo user exists in production
- [ ] Demo walkthrough works end-to-end
- [ ] `/memory/context` API documented in README

---

## 7-Day Summary

| Day | Focus | Key Output |
|-----|-------|------------|
| 1 | Foundation cleanup | Clean structure, pinned deps, env files |
| 2 | Alembic + user model | Migrations, user table, `user_id` everywhere |
| 3 | Auth layer | API keys, JWT, protected routes, user isolation |
| 4 | Engram upgrade | Async, real embeddings, deduplication, weighted graph |
| 5 | Context API + health | `/memory/context`, `/health/ready`, logging |
| 6 | Search + tests | HNSW, pagination, rate limiting, 7 tests passing |
| 7 | Deploy + demo | Live URL, polished dashboard, investor demo flow |

---

## What NOT to Build This Week

Do not spend time on:
- Blockchain / on-chain anchoring (Week 2 or 3)
- Token / tokenomics design
- Mobile app or React frontend
- Agent orchestration framework
- Multi-tenant org workspaces
- Advanced analytics or billing

**This week: hardened, deployed, demoable. Everything else is next week.**

---

## If You Get Stuck — Priority Order

If you run out of time, complete in this order and skip the rest:

1. Day 2: Migrations (non-negotiable)
2. Day 3: Auth + user isolation (non-negotiable)
3. Day 5: `/memory/context` endpoint (core product value)
4. Day 7: Deployment (makes it real)
5. Day 4: Engram upgrade (differentiator, but can be v0.2)
6. Day 6: Tests + HNSW (nice to have for v1)

