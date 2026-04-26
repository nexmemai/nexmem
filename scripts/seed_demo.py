"""
scripts/seed_demo.py — Standalone seed script for the demo user.

Usage:
    python scripts/seed_demo.py

What it does:
1.  Creates demo_user (email: demo@memorylayer.dev)
2.  Generates a named API key for demo_user, prints the raw key ONCE
3.  Seeds all 4 memory types with realistic data
4.  Verifies all rows were inserted

Run AFTER `alembic upgrade head`.
"""

import asyncio
import hashlib
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── allow running from project root ───────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import settings
from app.models.user import User, APIKey
from app.models.memory import (
    EpisodicMemory,
    SemanticMemory,
    ProceduralMemory,
    KnowledgeNode,
    KnowledgeEdge,
)

# ── helpers ───────────────────────────────────────────────────────────────────

DEMO_EMAIL = "demo@memorylayer.dev"
DEMO_API_KEY_NAME = "Demo CLI Key"


def _generate_api_key() -> tuple[str, str]:
    """Return (raw_key, sha256_hash). Store only the hash."""
    raw = f"mem_{secrets.token_urlsafe(32)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def _utc(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# ── seed functions ────────────────────────────────────────────────────────────

async def seed(session: AsyncSession) -> None:
    print("\n[SEED] Seeding demo data...")

    # ── 1. Create demo user ───────────────────────────────────────────────────
    existing = await session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": DEMO_EMAIL},
    )
    existing_row = existing.fetchone()

    if existing_row:
        demo_user_id = existing_row[0]
        print(f"   [OK] demo_user already exists  (id={demo_user_id})")
        
        # ── 1.5 Clean existing data for this user to ensure idempotency ───────
        print(f"   [CLEAN] Wiping existing data for user {demo_user_id}...")
        await session.execute(text("DELETE FROM knowledge_edges WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM knowledge_nodes WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM semantic_memory WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM episodic_memory WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM procedural_memory WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM api_keys WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.flush()
    else:
        demo_user_id = uuid.uuid4()
        demo_user = User(
            id=demo_user_id,
            email=DEMO_EMAIL,
            hashed_password=None,   # API key auth only for demo
            is_active=True,
        )
        session.add(demo_user)
        await session.flush()
        print(f"   [OK] Created demo_user        (id={demo_user_id})")

    # ── 2. Create API key ─────────────────────────────────────────────────────
    raw_key, key_hash = _generate_api_key()
    api_key_row = APIKey(
        user_id=demo_user_id,
        key_hash=key_hash,
        name=DEMO_API_KEY_NAME,
        scopes="read,write",
        is_active=True,
    )
    session.add(api_key_row)
    await session.flush()
    print(f"\n   [KEY] DEMO API KEY (shown ONCE — copy it now):")
    print(f"       {raw_key}\n")

    # ── 3. Episodic memories ──────────────────────────────────────────────────
    episodes = [
        EpisodicMemory(
            user_id=str(demo_user_id),
            session_id="seed-session-1",
            timestamp=_utc(days_ago=2),
            content="User asked about pgvector HNSW index performance.",
            metadata={"source": "chat", "agent": "assistant"},
            tags=["pgvector", "database", "performance"],
        ),
        EpisodicMemory(
            user_id=str(demo_user_id),
            session_id="seed-session-1",
            timestamp=_utc(days_ago=1),
            content="User prefers concise code examples without unnecessary comments.",
            metadata={"source": "preference_detection"},
            tags=["preference", "coding"],
        ),
        EpisodicMemory(
            user_id=str(demo_user_id),
            session_id="seed-session-2",
            timestamp=_utc(days_ago=0),
            content="User is building a decentralized AI memory layer with FastAPI.",
            metadata={"source": "context_window"},
            tags=["project", "fastapi", "memory"],
        ),
    ]
    for ep in episodes:
        session.add(ep)
    await session.flush()
    print(f"   [OK] Inserted {len(episodes)} episodic memories")

    # ── 4. Procedural memory (preferences) ───────────────────────────────────
    prefs = ProceduralMemory(
        user_id=str(demo_user_id),
        settings={
            "code_style": "concise",
            "response_format": "markdown",
            "preferred_language": "python",
            "timezone": "Asia/Kolkata",
        },
        workflows=[
            {"name": "daily_standup", "steps": ["review_tasks", "update_status"]},
            {"name": "code_review", "steps": ["read_pr", "run_tests", "comment"]},
        ],
    )
    session.add(prefs)
    await session.flush()
    print("   [OK] Inserted procedural memory (preferences)")

    # ── 5. Knowledge graph ────────────────────────────────────────────────────
    node_fastapi = KnowledgeNode(
        user_id=str(demo_user_id),
        label="FastAPI",
        type="technology",
        properties={"version": "0.115.0", "language": "python"},
    )
    node_pgvector = KnowledgeNode(
        user_id=str(demo_user_id),
        label="pgvector",
        type="technology",
        properties={"index_type": "HNSW"},
    )
    node_memorylayer = KnowledgeNode(
        user_id=str(demo_user_id),
        label="AI Memory Layer",
        type="project",
        properties={"status": "in_progress"},
    )
    session.add_all([node_fastapi, node_pgvector, node_memorylayer])
    await session.flush()

    edge1 = KnowledgeEdge(
        user_id=str(demo_user_id),
        from_node_id=node_memorylayer.id,
        to_node_id=node_fastapi.id,
        relation="uses",
        weight=1.0,
    )
    edge2 = KnowledgeEdge(
        user_id=str(demo_user_id),
        from_node_id=node_memorylayer.id,
        to_node_id=node_pgvector.id,
        relation="stores_vectors_in",
        weight=1.0,
    )
    session.add_all([edge1, edge2])
    await session.flush()
    print("   [OK] Inserted 3 knowledge nodes + 2 edges")

    await session.commit()
    print("\n[SUCCESS] Seed complete.")
    if raw_key.startswith("mem_"):
        print(f"\n   Use this header in API calls:")
        print(f'   Authorization: ApiKey {raw_key}\n')


# ── entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    if settings.demo_mode:
        print("⚠️   demo_mode=True in config — seed_demo.py targets PostgreSQL.")
        print("    Set DEMO_MODE=false in .env.local before running.\n")

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
