"""Demo mode database - SQLite-based for local testing without PostgreSQL."""

import uuid
import json
from datetime import datetime, timedelta
from typing import Optional, List, Any

# Fixed demo user ID (UUID string)
DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"

# In-memory storage for demo mode
episodic_store: dict[str, list[dict]] = {}
semantic_store: dict[str, list[dict]] = {}
procedural_store: dict[str, dict] = {}
graph_nodes_store: dict[str, list[dict]] = {}
graph_edges_store: dict[str, list[dict]] = {}


def generate_id() -> str:
    return str(uuid.uuid4())


def get_timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ==========================================
# Episodic Memory Operations
# ==========================================

def create_episodic(
    user_id: str,
    session_id: str,
    content: str,
    metadata: dict = None,
    tags: list[str] = None,
    store_episodic: bool = True,
) -> dict:
    if not store_episodic:
        return {"id": None, "skipped": True}

    record = {
        "id": generate_id(),
        "user_id": user_id,
        "session_id": session_id,
        "timestamp": get_timestamp(),
        "content": content,
        "metadata": metadata or {},
        "tags": tags or [],
        "store_episodic": store_episodic,
        "consolidated": False,
        "importance_score": 0.0,
        "app_id": None,  # New field for multi-app scoping
        "created_at": get_timestamp(),
    }

    if user_id not in episodic_store:
        episodic_store[user_id] = []
    episodic_store[user_id].append(record)

    return {"id": record["id"], "user_id": user_id, "created_at": record["created_at"]}


def get_episodic(user_id: str, limit: int = 50, session_id: str = None, app_id: str = None) -> list[dict]:
    records = episodic_store.get(user_id, [])
    if session_id:
        records = [r for r in records if r.get("session_id") == session_id]
    if app_id:
        records = [r for r in records if r.get("app_id") == app_id]
    records = sorted(records, key=lambda x: x.get("timestamp", ""), reverse=True)
    return records[:limit]


def count_episodic(user_id: str) -> int:
    return len(episodic_store.get(user_id, []))


def delete_episodic(user_id: str, episode_id: str) -> bool:
    records = episodic_store.get(user_id, [])
    for i, r in enumerate(records):
        if r["id"] == episode_id:
            records.pop(i)
            return True
    return False


def get_episodes_to_consolidate(user_id: str, days_old: int = 1) -> list[dict]:
    """Get unconsolidated episodes older than X days."""
    records = episodic_store.get(user_id, [])
    cutoff = (datetime.utcnow() - timedelta(days=days_old)).isoformat()
    return [
        r for r in records
        if not r.get("consolidated", False) and r.get("timestamp", "") < cutoff
    ]


def consolidate_episode_demo(episode: dict) -> bool:
    """Mark episode as consolidated in demo mode."""
    episode["consolidated"] = True
    episode["importance_score"] = 0.8  # Default score
    
    # Create semantic memory from episode
    from app.services.embedder import embedder
    try:
        vector = embedder.random_vector()  # Demo mode uses random vectors
        create_semantic(
            episode["user_id"],
            vector=vector,
            summary=episode["content"][:200],
            content_preview=episode["content"][:500],
            metadata={"source": "consolidation", "episode_id": episode["id"]},
        )
    except Exception as e:
        print(f"Failed to create semantic memory in demo: {e}")
    
    # Create graph node from first tag (demo simplification)
    if episode.get("tags"):
        try:
            create_node(
                episode["user_id"],
                label=episode["tags"][0],
                node_type="concept",
                properties={"source": "consolidation"},
            )
        except Exception as e:
            print(f"Failed to create graph node in demo: {e}")
    
    return True


# ==========================================
# Semantic Memory Operations
# ==========================================

def create_semantic(
    user_id: str,
    vector: list[float],
    episodic_id: str = None,
    embedding_model: str = "all-MiniLM-L6-v2",
    summary: str = None,
    content_preview: str = None,
    metadata: dict = None,
    index_semantic: bool = True,
) -> dict:
    if not index_semantic:
        return {"id": None, "skipped": True}

    record = {
        "id": generate_id(),
        "user_id": user_id,
        "episodic_id": episodic_id,
        "vector": vector,
        "embedding_model": embedding_model,
        "summary": summary,
        "content_preview": content_preview,
        "metadata": metadata or {},
        "index_semantic": index_semantic,
        "app_id": None,  # New field for multi-app scoping
        "created_at": get_timestamp(),
    }

    if user_id not in semantic_store:
        semantic_store[user_id] = []
    semantic_store[user_id].append(record)

    return {"id": record["id"], "user_id": user_id, "created_at": record["created_at"]}


def get_semantic(user_id: str, limit: int = 50) -> list[dict]:
    records = semantic_store.get(user_id, [])
    return records[:limit]


def count_semantic(user_id: str) -> int:
    return len(semantic_store.get(user_id, []))


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    import math
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def search_semantic(user_id: str, query_vector: list[float], k: int = 5, app_id: str = None) -> list[dict]:
    """Search semantic memories by vector similarity."""
    records = semantic_store.get(user_id, [])
    if not records:
        return []
    
    # Filter by app_id if provided
    if app_id:
        records = [r for r in records if r.get("app_id") == app_id]
    
    results = []
    for r in records:
        if r.get("vector"):
            sim = cosine_similarity(query_vector, r["vector"])
            results.append({
                **r,
                "similarity": round(sim, 4),
            })
    
    results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    return results[:k]


# ==========================================
# Procedural Memory Operations
# ==========================================

def upsert_procedural(
    user_id: str,
    settings: dict = None,
    workflows: list[dict] = None,
    store_procedural: bool = True,
) -> dict:
    record = {
        "id": generate_id(),
        "user_id": user_id,
        "settings": settings or {},
        "workflows": workflows or [],
        "store_procedural": store_procedural,
        "updated_at": get_timestamp(),
        "created_at": get_timestamp(),
    }

    procedural_store[user_id] = record
    return {"id": record["id"], "user_id": user_id, "upserted": True, "updated_at": record["updated_at"]}


def get_procedural(user_id: str) -> Optional[dict]:
    return procedural_store.get(user_id)


def count_procedural(user_id: str) -> int:
    return 1 if user_id in procedural_store else 0


# ==========================================
# Knowledge Graph Operations
# ==========================================

def create_node(user_id: str, label: str, node_type: str, properties: dict = None) -> dict:
    record = {
        "id": generate_id(),
        "user_id": user_id,
        "label": label,
        "type": node_type,
        "properties": properties or {},
        "store_associative": True,
        "app_id": None,  # New field for multi-app scoping
        "created_at": get_timestamp(),
    }

    if user_id not in graph_nodes_store:
        graph_nodes_store[user_id] = []
    graph_nodes_store[user_id].append(record)

    return record


def get_nodes(user_id: str, node_type: str = None, limit: int = 100, app_id: str = None) -> list[dict]:
    records = graph_nodes_store.get(user_id, [])
    if node_type:
        records = [r for r in records if r.get("type") == node_type]
    # Filter by app_id if provided
    if app_id:
        records = [r for r in records if r.get("app_id") == app_id]
    return records[:limit]


def get_node(user_id: str, node_id: str) -> Optional[dict]:
    records = graph_nodes_store.get(user_id, [])
    for r in records:
        if r["id"] == node_id:
            return r
    return None


def delete_node(user_id: str, node_id: str) -> bool:
    records = graph_nodes_store.get(user_id, [])
    for i, r in enumerate(records):
        if r["id"] == node_id:
            records.pop(i)
            # Also delete related edges
            edges = graph_edges_store.get(user_id, [])
            graph_edges_store[user_id] = [
                e for e in edges
                if e["from_node_id"] != node_id and e["to_node_id"] != node_id
            ]
            return True
    return False


def count_nodes(user_id: str) -> int:
    return len(graph_nodes_store.get(user_id, []))


def create_edge(
    user_id: str,
    from_node_id: str,
    to_node_id: str,
    relation: str,
    weight: float = 1.0,
    metadata: dict = None,
) -> dict:
    # Check self-loop
    if from_node_id == to_node_id:
        raise ValueError("Self-loops are not allowed")

    record = {
        "id": generate_id(),
        "user_id": user_id,
        "from_node_id": from_node_id,
        "to_node_id": to_node_id,
        "relation": relation,
        "weight": weight,
        "metadata": metadata or {},
        "app_id": None,  # New field for multi-app scoping
        "created_at": get_timestamp(),
    }

    if user_id not in graph_edges_store:
        graph_edges_store[user_id] = []
    graph_edges_store[user_id].append(record)

    return record


def get_edges(user_id: str, node_id: str = None, limit: int = 200, app_id: str = None) -> list[dict]:
    records = graph_edges_store.get(user_id, [])
    if node_id:
        records = [r for r in records if r.get("from_node_id") == node_id or r.get("to_node_id") == node_id]
    # Filter by app_id if provided
    if app_id:
        records = [r for r in records if r.get("app_id") == app_id]
    return records[:limit]


def count_edges(user_id: str) -> int:
    return len(graph_edges_store.get(user_id, []))


def find_path(user_id: str, from_node_id: str, to_node_id: str, max_hops: int = 3) -> dict:
    """BFS path finding."""
    from collections import deque

    visited = {from_node_id}
    queue = deque([(from_node_id, [from_node_id])])

    while queue:
        current, path = queue.popleft()

        if current == to_node_id:
            # Get node details
            nodes = []
            for nid in path:
                node = get_node(user_id, nid)
                if node:
                    nodes.append(node)
            return {"found": True, "path": path, "hops": len(path) - 1, "nodes": nodes}

        if len(path) - 1 >= max_hops:
            continue

        edges = get_edges(user_id, node_id=current)
        for edge in edges:
            neighbor = edge["to_node_id"]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return {"found": False, "path": [], "hops": 0, "nodes": []}


def get_recent_memories(user_id: str, limit: int = 10) -> list[dict]:
    """Get recent memories across all types."""
    memories = []

    # Episodic
    for ep in get_episodic(user_id, limit=limit):
        memories.append({
            "memory_type": "episodic",
            "id": ep["id"],
            "content": ep["content"][:200],
            "created_at": ep["created_at"],
            "emoji_label": "🧠 Episodic",
        })

    # Semantic
    for sm in get_semantic(user_id, limit=limit):
        memories.append({
            "memory_type": "semantic",
            "id": sm["id"],
            "content": sm.get("summary") or sm.get("content_preview", "")[:200],
            "created_at": sm["created_at"],
            "emoji_label": "🔍 Semantic",
        })

    # Procedural
    proc = get_procedural(user_id)
    if proc:
        memories.append({
            "memory_type": "procedural",
            "id": proc["id"],
            "content": f"Settings: {json.dumps(proc.get('settings', {}))[:100]}",
            "created_at": proc["updated_at"],
            "emoji_label": "⚙️ Procedural",
        })

    # Sort by created_at descending
    memories.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return memories[:limit]


def get_memory_stats(user_id: str) -> dict:
    """Get memory statistics."""
    return {
        "user_id": user_id,
        "episodic_count": count_episodic(user_id),
        "semantic_count": count_semantic(user_id),
        "procedural_count": count_procedural(user_id),
        "graph_node_count": count_nodes(user_id),
        "graph_edge_count": count_edges(user_id),
        "total_memories": (
            count_episodic(user_id) +
            count_semantic(user_id) +
            count_procedural(user_id) +
            count_nodes(user_id)
        ),
    }


# ==========================================
# Initialize Demo Data
# ==========================================

def initialize_demo_data(user_id: str = DEMO_USER_ID):
    """Initialize demo data for testing."""
    import random
 
    # Procedural
    upsert_procedural(
        DEMO_USER_ID,
        settings={
            "theme": "dark",
            "language": "en",
            "notifications": True,
            "timezone": "UTC",
            "response_style": "concise",
            "preferred_model": "gpt-4o",
        },
        workflows=[
            {"id": "wf_001", "name": "daily_briefing", "trigger": "8am", "actions": ["fetch_news", "summarize"], "enabled": True},
            {"id": "wf_002", "name": "weekly_review", "trigger": "friday", "actions": ["collect_logs", "generate_report"], "enabled": True},
            {"id": "wf_003", "name": "project_tracker", "trigger": "on_mention", "actions": ["log_task", "update_board"], "enabled": True},
        ],
    )

    # Episodic memories
    episodic_data = [
        ("session_001", "User asked about project management tools and showed interest in Notion and Linear for tracking AI startup projects.", ["project-management", "notion", "linear"]),
        ("session_001", "User prefers concise bullet-point responses over long paragraphs. Values efficiency in AI interactions.", ["preference", "response-style", "communication"]),
        ("session_001", "User is building a new startup in the AI infrastructure space, focusing on memory systems for LLMs.", ["context", "startup", "ai-infrastructure"]),
        ("session_002", "User asked for help debugging a Python async issue with FastAPI and asyncpg. Resolved the connection pool timeout.", ["coding", "fastapi", "debugging", "async"]),
        ("session_002", "User wants to set up a persistent memory layer for their AI agent. Discussed pgvector and PostgreSQL as the storage backend.", ["ai", "pgvector", "memory-layer", "postgresql"]),
    ]

    episodic_ids = []
    for session_id, content, tags in episodic_data:
        result = create_episodic(DEMO_USER_ID, session_id, content, {"source": "demo"}, tags)
        episodic_ids.append(result["id"])

    # Semantic memories with random vectors
    semantic_data = [
        ("Interest in project management tools — Notion and Linear", episodic_ids[0] if episodic_ids else None),
        ("User prefers concise bullet-point communication style", episodic_ids[1] if len(episodic_ids) > 1 else None),
        ("Building AI infrastructure startup focused on LLM memory systems", episodic_ids[2] if len(episodic_ids) > 2 else None),
        ("Python async debugging with FastAPI — connection pool timeout", episodic_ids[3] if len(episodic_ids) > 3 else None),
        ("AI memory layer using pgvector and PostgreSQL", episodic_ids[4] if len(episodic_ids) > 4 else None),
    ]

    for summary, ep_id in semantic_data:
        # Generate a random normalized vector
        vector = [random.gauss(0, 1) for _ in range(384)]
        norm = sum(v * v for v in vector) ** 0.5
        vector = [v / norm for v in vector]

        create_semantic(
            DEMO_USER_ID,
            vector=vector,
            episodic_id=ep_id,
            summary=summary,
            content_preview=summary,
            metadata={"source": "demo"},
        )

    # Knowledge graph nodes
    node_data = [
        ("AI Infrastructure", "domain", {"description": "The field of AI infrastructure and tooling"}),
        ("pgvector", "technology", {"description": "PostgreSQL vector extension for similarity search"}),
        ("Memory Layer", "concept", {"description": "Persistent memory system for AI agents"}),
        ("FastAPI", "framework", {"description": "Modern Python web framework for building APIs"}),
        ("Notion", "tool", {"description": "All-in-one workspace and project management tool"}),
        ("AI Agent", "concept", {"description": "Autonomous or semi-autonomous AI system"}),
    ]

    node_ids = []
    for label, node_type, props in node_data:
        node = create_node(DEMO_USER_ID, label, node_type, props)
        node_ids.append(node["id"])

    # Knowledge graph edges
    edge_data = [
        (2, 0, "part_of", 0.9),  # Memory Layer -> AI Infrastructure
        (5, 2, "uses", 0.95),  # AI Agent -> Memory Layer
        (1, 0, "enables", 0.85),  # pgvector -> AI Infrastructure
        (3, 2, "hosts", 0.8),  # FastAPI -> Memory Layer
        (4, 5, "integrates_with", 0.6),  # Notion -> AI Agent
    ]

    for from_idx, to_idx, relation, weight in edge_data:
        if from_idx < len(node_ids) and to_idx < len(node_ids):
            try:
                create_edge(DEMO_USER_ID, node_ids[from_idx], node_ids[to_idx], relation, weight)
            except ValueError:
                pass

    return get_memory_stats(DEMO_USER_ID)
