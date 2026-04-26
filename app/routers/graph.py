"""Knowledge graph (associative memory) API endpoints."""

from collections import deque
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.config import settings

router = APIRouter(prefix="/agents", tags=["associative"])


# ==========================================
# Node Endpoints
# ==========================================

@router.post("/{user_id}/graph/nodes")
async def create_node(
    user_id: str,
    label: str,
    type: str,
    properties: dict = {},
):
    """Create a new knowledge graph node."""
    if settings.demo_mode:
        from app.demo_db import create_node as demo_create_node
        return demo_create_node(user_id, label, type, properties)

    from app.database import get_db
    from app.models.memory import KnowledgeNode

    async for db in get_db():
        record = KnowledgeNode(
            user_id=user_id, label=label, type=type,
            properties=properties, store_associative=True,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return {
            "id": str(record.id), "user_id": record.user_id,
            "label": record.label, "type": record.type,
            "properties": record.properties,
            "store_associative": record.store_associative,
            "created_at": record.created_at.isoformat(),
        }


@router.get("/{user_id}/graph/nodes")
async def list_nodes(
    user_id: str,
    node_type: Optional[str] = Query(None),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List knowledge graph nodes for a user."""
    if settings.demo_mode:
        from app.demo_db import get_nodes
        return get_nodes(user_id, node_type=node_type, limit=limit)

    from app.database import get_db
    from app.models.memory import KnowledgeNode
    from sqlalchemy import select

    async for db in get_db():
        query = (
            select(KnowledgeNode)
            .where(KnowledgeNode.user_id == user_id)
            .where(KnowledgeNode.store_associative == True)
        )
        if node_type:
            query = query.where(KnowledgeNode.type == node_type)
        query = query.limit(limit)
        result = await db.execute(query)
        records = result.scalars().all()
        return [
            {
                "id": str(r.id), "user_id": r.user_id, "label": r.label,
                "type": r.type, "properties": r.properties,
                "store_associative": r.store_associative,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]


@router.delete("/{user_id}/graph/nodes/{node_id}")
async def delete_node(user_id: str, node_id: str):
    """Delete a knowledge graph node and its edges."""
    if settings.demo_mode:
        from app.demo_db import delete_node
        if not delete_node(user_id, node_id):
            raise HTTPException(status_code=404, detail="Node not found")
        return {"deleted": True, "id": node_id}

    from app.database import get_db
    from app.models.memory import KnowledgeNode
    from sqlalchemy import select

    async for db in get_db():
        result = await db.execute(
            select(KnowledgeNode).where(
                KnowledgeNode.id == node_id, KnowledgeNode.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Node not found")
        await db.delete(record)
        await db.commit()
        return {"deleted": True, "id": node_id}


# ==========================================
# Edge Endpoints
# ==========================================

@router.post("/{user_id}/graph/edges")
async def create_edge(
    user_id: str,
    from_node_id: str,
    to_node_id: str,
    relation: str,
    weight: float = 1.0,
    metadata: dict = {},
):
    """Create a new knowledge graph edge."""
    if settings.demo_mode:
        from app.demo_db import create_edge as demo_create_edge, get_node
        if not get_node(user_id, from_node_id) or not get_node(user_id, to_node_id):
            raise HTTPException(status_code=404, detail="One or both nodes not found")
        try:
            return demo_create_edge(user_id, from_node_id, to_node_id, relation, weight, metadata)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    from app.database import get_db
    from app.models.memory import KnowledgeEdge, KnowledgeNode

    async for db in get_db():
        from_node = await db.get(KnowledgeNode, from_node_id)
        to_node = await db.get(KnowledgeNode, to_node_id)
        if not from_node or not to_node:
            raise HTTPException(status_code=404, detail="One or both nodes not found")
        if from_node.user_id != user_id or to_node.user_id != user_id:
            raise HTTPException(status_code=403, detail="Nodes do not belong to this user")
        if from_node_id == to_node_id:
            raise HTTPException(status_code=400, detail="Self-loops are not allowed")

        record = KnowledgeEdge(
            user_id=user_id, from_node_id=from_node_id, to_node_id=to_node_id,
            relation=relation, weight=weight, metadata=metadata,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return {
            "id": str(record.id), "user_id": record.user_id,
            "from_node_id": str(record.from_node_id),
            "to_node_id": str(record.to_node_id),
            "relation": record.relation, "weight": record.weight,
            "metadata": record.metadata, "created_at": record.created_at.isoformat(),
        }


@router.get("/{user_id}/graph/edges")
async def list_edges(
    user_id: str,
    node_id: Optional[str] = Query(None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """List knowledge graph edges for a user."""
    if settings.demo_mode:
        from app.demo_db import get_edges
        return get_edges(user_id, node_id=node_id, limit=limit)

    from app.database import get_db
    from app.models.memory import KnowledgeEdge
    from sqlalchemy import select

    async for db in get_db():
        query = select(KnowledgeEdge).where(KnowledgeEdge.user_id == user_id)
        if node_id:
            query = query.where(
                (KnowledgeEdge.from_node_id == node_id) | (KnowledgeEdge.to_node_id == node_id)
            )
        query = query.limit(limit)
        result = await db.execute(query)
        records = result.scalars().all()
        return [
            {
                "id": str(r.id), "user_id": r.user_id,
                "from_node_id": str(r.from_node_id), "to_node_id": str(r.to_node_id),
                "relation": r.relation, "weight": r.weight,
                "metadata": r.metadata, "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]


@router.post("/{user_id}/graph/path")
async def find_path(
    user_id: str,
    from_node_id: str,
    to_node_id: str,
    max_hops: int = Query(default=3, ge=1, le=10),
):
    """Find a path between two nodes using BFS."""
    if settings.demo_mode:
        from app.demo_db import find_path, get_node
        if not get_node(user_id, from_node_id) or not get_node(user_id, to_node_id):
            raise HTTPException(status_code=404, detail="One or both nodes not found")
        return find_path(user_id, from_node_id, to_node_id, max_hops)

    from app.database import get_db
    from app.models.memory import KnowledgeNode, KnowledgeEdge
    from sqlalchemy import select

    async for db in get_db():
        from_node = await db.get(KnowledgeNode, from_node_id)
        to_node = await db.get(KnowledgeNode, to_node_id)
        if not from_node or not to_node:
            raise HTTPException(status_code=404, detail="One or both nodes not found")

        visited = {from_node_id}
        queue = deque([(from_node_id, [from_node_id])])

        while queue:
            current_id, path = queue.popleft()
            if current_id == to_node_id:
                node_details = []
                for nid in path:
                    node = await db.get(KnowledgeNode, nid)
                    if node:
                        node_details.append({
                            "id": str(node.id), "label": node.label,
                            "type": node.type, "properties": node.properties,
                        })
                return {"found": True, "path": path, "hops": len(path) - 1, "nodes": node_details}

            if len(path) - 1 >= max_hops:
                continue

            result = await db.execute(
                select(KnowledgeEdge).where(
                    KnowledgeEdge.from_node_id == current_id,
                    KnowledgeEdge.user_id == user_id,
                )
            )
            edges = result.scalars().all()
            for edge in edges:
                neighbor_id = str(edge.to_node_id)
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, path + [neighbor_id]))

        return {"found": False, "path": [], "hops": 0, "nodes": []}


@router.get("/{user_id}/graph/stats")
async def get_graph_stats(user_id: str):
    """Get statistics about the knowledge graph."""
    if settings.demo_mode:
        from app.demo_db import get_nodes, get_edges
        nodes = get_nodes(user_id)
        edges = get_edges(user_id)

        node_types = {}
        for n in nodes:
            node_types[n.get("type", "unknown")] = node_types.get(n.get("type", "unknown"), 0) + 1

        relation_counts = {}
        for e in edges:
            relation_counts[e.get("relation", "unknown")] = relation_counts.get(e.get("relation", "unknown"), 0) + 1

        return {
            "user_id": user_id, "total_nodes": len(nodes), "total_edges": len(edges),
            "node_types": node_types, "relation_counts": relation_counts,
            "density": len(edges) / max(len(nodes) * (len(nodes) - 1), 1) if nodes else 0,
        }

    from app.database import get_db
    from app.models.memory import KnowledgeNode, KnowledgeEdge
    from sqlalchemy import select

    async for db in get_db():
        nodes_result = await db.execute(
            select(KnowledgeNode).where(KnowledgeNode.user_id == user_id)
        )
        nodes = nodes_result.scalars().all()
        edges_result = await db.execute(
            select(KnowledgeEdge).where(KnowledgeEdge.user_id == user_id)
        )
        edges = edges_result.scalars().all()

        node_types = {}
        for node in nodes:
            node_types[node.type] = node_types.get(node.type, 0) + 1
        relation_counts = {}
        for edge in edges:
            relation_counts[edge.relation] = relation_counts.get(edge.relation, 0) + 1

        return {
            "user_id": user_id, "total_nodes": len(nodes), "total_edges": len(edges),
            "node_types": node_types, "relation_counts": relation_counts,
            "density": len(edges) / max(len(nodes) * (len(nodes) - 1), 1) if nodes else 0,
        }
