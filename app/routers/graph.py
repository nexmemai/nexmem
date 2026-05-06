"""Knowledge graph (associative memory) API endpoints."""

import uuid
from collections import deque
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/agents", tags=["associative"])


class NodeCreateRequest(BaseModel):
    label: str
    type: str
    properties: dict = Field(default_factory=dict)


class EdgeCreateRequest(BaseModel):
    from_node_id: str
    to_node_id: str
    relation: str
    weight: float = 1.0
    metadata: dict = Field(default_factory=dict)


class PathRequest(BaseModel):
    from_node_id: str
    to_node_id: str


# ==========================================
# Node Endpoints
# ==========================================

@router.post("/{user_id}/graph/nodes")
async def create_node(
    user_id: str,
    body: NodeCreateRequest,
    app_id: Optional[str] = Query(default=None, description="App ID for scoping"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new knowledge graph node."""
    # Validate path user_id matches authenticated user
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    if settings.demo_mode:
        from app.demo_db import create_node as demo_create_node
        return demo_create_node(str(current_user.id), body.label, body.type, body.properties)

    from app.models.memory import KnowledgeNode
    
    record = KnowledgeNode(
        user_id=str(current_user.id), label=body.label, type=body.type,
        properties=body.properties, store_associative=True,
    )
    # Add app_id if provided
    if app_id:
        try:
            record.app_id = uuid.UUID(app_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {
        "id": str(record.id), "user_id": str(record.user_id),
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
    offset: int = Query(default=0, ge=0),
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List knowledge graph nodes for a user (paginated)."""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")

    if settings.demo_mode:
        from app.demo_db import get_nodes
        return get_nodes(user_id, node_type=node_type, limit=limit)

    from app.models.memory import KnowledgeNode
    from sqlalchemy import select

    query = (
        select(KnowledgeNode)
        .where(KnowledgeNode.user_id == str(current_user.id))
        .where(KnowledgeNode.store_associative == True)
    )
    if node_type:
        query = query.where(KnowledgeNode.type == node_type)
    if app_id:
        try:
            query = query.where(KnowledgeNode.app_id == uuid.UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    query = query.order_by(KnowledgeNode.created_at).offset(offset).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()
    return [
        {
            "id": str(r.id), "user_id": str(r.user_id), "label": r.label,
            "type": r.type, "properties": r.properties,
            "store_associative": r.store_associative,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


@router.delete("/{user_id}/graph/nodes/{node_id}")
async def delete_node(
    user_id: str,
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a knowledge graph node and its edges."""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")

    if settings.demo_mode:
        from app.demo_db import delete_node
        if not delete_node(user_id, node_id):
            raise HTTPException(status_code=404, detail="Node not found")
        return {"deleted": True, "id": node_id}

    from app.models.memory import KnowledgeNode

    result = await db.execute(
        select(KnowledgeNode).where(
            KnowledgeNode.id == node_id,
            KnowledgeNode.user_id == str(current_user.id),
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Node not found")

    await db.delete(record)
    await db.commit()
    return {"deleted": True, "id": node_id}


# ── Edge Endpoints ─────────────────────────────────────────────────────────────

@router.post("/{user_id}/graph/edges")
async def create_edge(
    user_id: str,
    body: EdgeCreateRequest,
    app_id: Optional[str] = Query(default=None, description="App ID for scoping"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new knowledge graph edge."""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")

    if settings.demo_mode:
        from app.demo_db import create_edge as demo_create_edge, get_node
        if not get_node(str(current_user.id), body.from_node_id) or not get_node(str(current_user.id), body.to_node_id):
            raise HTTPException(status_code=404, detail="One or both nodes not found")
        try:
            return demo_create_edge(
                str(current_user.id),
                body.from_node_id, body.to_node_id,
                body.relation, body.weight, body.metadata,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    from app.models.memory import KnowledgeNode
    from app.services.consolidation import persist_edge

    from_node = await db.get(KnowledgeNode, body.from_node_id)
    to_node = await db.get(KnowledgeNode, body.to_node_id)
    if not from_node or not to_node:
        raise HTTPException(status_code=404, detail="One or both nodes not found")
    if str(from_node.user_id) != str(current_user.id) or str(to_node.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Nodes do not belong to this user")
    if body.from_node_id == body.to_node_id:
        raise HTTPException(status_code=400, detail="Self-loops are not allowed")

    edge_app_id = None
    if app_id:
        try:
            edge_app_id = uuid.UUID(app_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")

    record = await persist_edge(
        db,
        source_id=body.from_node_id,
        target_id=body.to_node_id,
        relation=body.relation,
        weight=body.weight,
        user_id=str(current_user.id),
        app_id=edge_app_id,
        metadata=body.metadata,
    )
    await db.commit()
    await db.refresh(record)
    return {
        "id": str(record.id), "user_id": str(record.user_id),
        "from_node_id": str(record.from_node_id), "to_node_id": str(record.to_node_id),
        "relation": record.relation, "weight": record.weight,
        "metadata": record.extra_metadata, "created_at": record.created_at.isoformat(),
    }


@router.get("/{user_id}/graph/edges")
async def list_edges(
    user_id: str,
    node_id: Optional[str] = Query(None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List knowledge graph edges for a user (paginated)."""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")

    if settings.demo_mode:
        from app.demo_db import get_edges
        return get_edges(str(current_user.id), node_id=node_id, limit=limit)

    from app.models.memory import KnowledgeEdge
    from sqlalchemy import select

    query = select(KnowledgeEdge).where(KnowledgeEdge.user_id == str(current_user.id))
    if node_id:
        query = query.where(
            (KnowledgeEdge.from_node_id == node_id) | (KnowledgeEdge.to_node_id == node_id)
        )
    if app_id:
        try:
            query = query.where(KnowledgeEdge.app_id == uuid.UUID(app_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")
    query = query.order_by(KnowledgeEdge.created_at).offset(offset).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()
    return [
        {
            "id": str(r.id), "user_id": str(r.user_id),
            "from_node_id": str(r.from_node_id), "to_node_id": str(r.to_node_id),
            "relation": r.relation, "weight": r.weight,
            "metadata": r.extra_metadata, "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


@router.post("/{user_id}/graph/path")
async def find_path(
    user_id: str,
    body: PathRequest,
    max_hops: int = Query(default=3, ge=1, le=10),
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Find a path between two nodes using BFS."""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")

    if settings.demo_mode:
        from app.demo_db import find_path as demo_find_path, get_node
        if not get_node(str(current_user.id), body.from_node_id) or not get_node(str(current_user.id), body.to_node_id):
            raise HTTPException(status_code=404, detail="One or both nodes not found")
        return demo_find_path(str(current_user.id), body.from_node_id, body.to_node_id, max_hops)

    from app.models.memory import KnowledgeNode, KnowledgeEdge
    from sqlalchemy import select

    from_node = await db.get(KnowledgeNode, body.from_node_id)
    to_node = await db.get(KnowledgeNode, body.to_node_id)
    if not from_node or not to_node:
        raise HTTPException(status_code=404, detail="One or both nodes not found")

    if str(from_node.user_id) != str(current_user.id) or str(to_node.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Nodes do not belong to user")

    if app_id:
        try:
            app_id_uuid = uuid.UUID(app_id)
            if from_node.app_id != app_id_uuid or to_node.app_id != app_id_uuid:
                raise HTTPException(status_code=403, detail="Nodes do not belong to specified app")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")

    visited = {body.from_node_id}
    queue = deque([(body.from_node_id, [body.from_node_id])])

    while queue:
        current_id, path = queue.popleft()
        if current_id == body.to_node_id:
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

        edge_query = select(KnowledgeEdge).where(
            KnowledgeEdge.from_node_id == current_id,
            KnowledgeEdge.user_id == str(current_user.id),
        )
        if app_id:
            try:
                edge_query = edge_query.where(KnowledgeEdge.app_id == uuid.UUID(app_id))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid app_id format")

        result = await db.execute(edge_query)
        edges = result.scalars().all()
        for edge in edges:
            neighbor_id = str(edge.to_node_id)
            if neighbor_id not in visited:
                visited.add(neighbor_id)
                queue.append((neighbor_id, path + [neighbor_id]))

    return {"found": False, "path": [], "hops": 0, "nodes": []}


@router.get("/{user_id}/graph/stats")
async def get_graph_stats(
    user_id: str,
    app_id: Optional[str] = Query(default=None, description="Filter by app ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get statistics about the knowledge graph using SQL aggregates (no full scan)."""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="User ID mismatch")

    if settings.demo_mode:
        from app.demo_db import get_nodes, get_edges
        nodes = get_nodes(str(current_user.id))
        edges = get_edges(str(current_user.id))
        node_types: dict = {}
        for n in nodes:
            node_types[n.get("type", "unknown")] = node_types.get(n.get("type", "unknown"), 0) + 1
        relation_counts: dict = {}
        for e in edges:
            relation_counts[e.get("relation", "unknown")] = relation_counts.get(e.get("relation", "unknown"), 0) + 1
        return {
            "user_id": str(current_user.id), "total_nodes": len(nodes), "total_edges": len(edges),
            "node_types": node_types, "relation_counts": relation_counts,
            "density": len(edges) / max(len(nodes) * (len(nodes) - 1), 1) if nodes else 0,
        }

    from app.models.memory import KnowledgeNode, KnowledgeEdge
    from sqlalchemy import select, func as sqlfunc

    app_id_uuid = None
    if app_id:
        try:
            app_id_uuid = uuid.UUID(app_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app_id format")

    # ── SQL COUNT queries — no full table scan into Python memory ─────────────
    node_count_q = select(sqlfunc.count()).where(KnowledgeNode.user_id == str(current_user.id))
    edge_count_q = select(sqlfunc.count()).where(KnowledgeEdge.user_id == str(current_user.id))
    if app_id_uuid:
        node_count_q = node_count_q.where(KnowledgeNode.app_id == app_id_uuid)
        edge_count_q = edge_count_q.where(KnowledgeEdge.app_id == app_id_uuid)

    total_nodes = (await db.execute(node_count_q)).scalar() or 0
    total_edges = (await db.execute(edge_count_q)).scalar() or 0

    # Type/relation breakdown — capped at top 50 per category to avoid huge responses
    node_type_q = (
        select(KnowledgeNode.type, sqlfunc.count().label("cnt"))
        .where(KnowledgeNode.user_id == str(current_user.id))
        .group_by(KnowledgeNode.type)
        .order_by(sqlfunc.count().desc())
        .limit(50)
    )
    edge_rel_q = (
        select(KnowledgeEdge.relation, sqlfunc.count().label("cnt"))
        .where(KnowledgeEdge.user_id == str(current_user.id))
        .group_by(KnowledgeEdge.relation)
        .order_by(sqlfunc.count().desc())
        .limit(50)
    )
    if app_id_uuid:
        node_type_q = node_type_q.where(KnowledgeNode.app_id == app_id_uuid)
        edge_rel_q = edge_rel_q.where(KnowledgeEdge.app_id == app_id_uuid)

    node_types = {row[0]: row[1] for row in (await db.execute(node_type_q)).all()}
    relation_counts = {row[0]: row[1] for row in (await db.execute(edge_rel_q)).all()}

    return {
        "user_id": str(current_user.id),
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "node_types": node_types,
        "relation_counts": relation_counts,
        "density": total_edges / max(total_nodes * (total_nodes - 1), 1) if total_nodes else 0,
    }
