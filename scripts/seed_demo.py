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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text

from app.config import settings
from app.models.user import User, APIKey
from app.models.memory import (
    EpisodicMemory,
    SemanticMemory,
    ProceduralMemory,
    KnowledgeNode,
    KnowledgeEdge,
)

DEMO_EMAIL = "demo@memorylayer.dev"
DEMO_API_KEY_NAME = "Demo CLI Key"


def _generate_api_key() -> tuple[str, str]:
    """Return (raw_key, sha256_hash). Store only the hash."""
    raw = f"mem_{secrets.token_urlsafe(32)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def _utc(days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


async def seed(session: AsyncSession) -> None:
    print("\n[SEED] Seeding demo data...")

    existing = await session.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": DEMO_EMAIL},
    )
    existing_row = existing.fetchone()

    if existing_row:
        demo_user_id = str(existing_row[0])
        print(f"   [OK] demo_user already exists  (id={demo_user_id})")

        print(f"   [CLEAN] Wiping existing data for user {demo_user_id}...")
        await session.execute(text("DELETE FROM knowledge_edges WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM knowledge_nodes WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM semantic_memory WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM episodic_memory WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM procedural_memory WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.execute(text("DELETE FROM api_keys WHERE user_id = :uid"), {"uid": demo_user_id})
        await session.flush()
    else:
        demo_user_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO users (id, email, hashed_password, is_active)
                VALUES (:id, :email, :hash, true)
            """),
            {
                "id": demo_user_id,
                "email": DEMO_EMAIL,
                "hash": None,
            }
        )
        await session.flush()
        print(f"   [OK] Created demo_user        (id={demo_user_id})")

    raw_key, key_hash = _generate_api_key()
    api_key_id = str(uuid.uuid4())
    await session.execute(
        text("""
            INSERT INTO api_keys (id, user_id, key_hash, name, scopes, is_active)
            VALUES (:id, :uid, :hash, :name, 'read,write', true)
        """),
        {
            "id": api_key_id,
            "uid": demo_user_id,
            "hash": key_hash,
            "name": DEMO_API_KEY_NAME,
        }
    )
    await session.flush()
    print(f"\n   [KEY] DEMO API KEY (shown ONCE — copy it now):")
    print(f"       {raw_key}\n")

    session_id_1 = "seed-session-1"
    session_id_2 = "seed-session-2"

    episodes_data = [
        {
            "session_id": session_id_1,
            "timestamp": _utc(days_ago=2).isoformat(),
            "content": "User asked about pgvector HNSW index performance.",
            "metadata": {"source": "chat", "agent": "assistant"},
            "tags": ["pgvector", "database", "performance"],
        },
        {
            "session_id": session_id_1,
            "timestamp": _utc(days_ago=1).isoformat(),
            "content": "User prefers concise code examples without unnecessary comments.",
            "metadata": {"source": "preference_detection"},
            "tags": ["preference", "coding"],
        },
        {
            "session_id": session_id_2,
            "timestamp": _utc(days_ago=0).isoformat(),
            "content": "User is building a decentralized AI memory layer with FastAPI.",
            "metadata": {"source": "context_window"},
            "tags": ["project", "fastapi", "memory"],
        },
    ]

    for ep_data in episodes_data:
        ep_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO episodic_memory (id, user_id, session_id, timestamp, content, metadata, tags)
                VALUES (:id, :uid, :session, :timestamp, :content, :meta, :tags)
            """),
            {
                "id": ep_id,
                "uid": demo_user_id,
                "session": ep_data["session_id"],
                "timestamp": ep_data["timestamp"],
                "content": ep_data["content"],
                "meta": ep_data["metadata"],
                "tags": ep_data["tags"],
            }
        )
    print(f"   [OK] Inserted {len(episodes_data)} episodic memories")

    preferences = {
        "code_style": "concise",
        "response_format": "markdown",
        "preferred_language": "python",
        "timezone": "Asia/Kolkata",
    }
    workflows = [
        {"name": "daily_standup", "steps": ["review_tasks", "update_status"]},
        {"name": "code_review", "steps": ["read_pr", "run_tests", "comment"]},
    ]
    await session.execute(
        text("""
            INSERT INTO procedural_memory (id, user_id, settings, workflows, store_procedural)
            VALUES (:id, :uid, :settings, :workflows, true)
            ON CONFLICT (user_id) DO UPDATE SET settings = :settings, workflows = :workflows
        """),
        {
            "id": str(uuid.uuid4()),
            "uid": demo_user_id,
            "settings": preferences,
            "workflows": workflows,
        }
    )
    await session.flush()
    print("   [OK] Inserted procedural memory (preferences)")

    node_fastapi_id = str(uuid.uuid4())
    node_pgvector_id = str(uuid.uuid4())
    node_memorylayer_id = str(uuid.uuid4())

    nodes = [
        (node_fastapi_id, "FastAPI", "technology", {"version": "0.115.0", "language": "python"}),
        (node_pgvector_id, "pgvector", "technology", {"index_type": "HNSW"}),
        (node_memorylayer_id, "AI Memory Layer", "project", {"status": "in_progress"}),
    ]

    for node_id, label, node_type, props in nodes:
        await session.execute(
            text("""
                INSERT INTO knowledge_nodes (id, user_id, label, type, properties, store_associative)
                VALUES (:id, :uid, :label, :type, :props, true)
            """),
            {
                "id": node_id,
                "uid": demo_user_id,
                "label": label,
                "type": node_type,
                "props": props,
            }
        )
    await session.flush()

    edges = [
        (node_memorylayer_id, node_fastapi_id, "uses", 1.0),
        (node_memorylayer_id, node_pgvector_id, "stores_vectors_in", 1.0),
    ]

    for from_id, to_id, relation, weight in edges:
        await session.execute(
            text("""
                INSERT INTO knowledge_edges (id, user_id, from_node_id, to_node_id, relation, weight)
                VALUES (:id, :uid, :from, :to, :relation, :weight)
            """),
            {
                "id": str(uuid.uuid4()),
                "uid": demo_user_id,
                "from": from_id,
                "to": to_id,
                "relation": relation,
                "weight": weight,
            }
        )
    await session.flush()
    print("   [OK] Inserted 3 knowledge nodes + 2 edges")

    await session.commit()
    print("\n[SUCCESS] Seed complete.")
    if raw_key.startswith("mem_"):
        print(f"\n   Use this header in API calls:")
        print(f'   Authorization: ApiKey {raw_key}\n')


async def main() -> None:
    if settings.demo_mode:
        print("WARNING: demo_mode=True in config — seed_demo.py targets PostgreSQL.")
        print("    Set DEMO_MODE=false in .env.local before running.\n")

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())