"""Hybrid Retrieval Service - Combines vector, keyword, and graph search."""

import logging
import uuid
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import SemanticMemory, EpisodicMemory, KnowledgeNode, KnowledgeEdge
from app.services.embedder import embedder
from app.config import settings

logger = logging.getLogger(__name__)

# ==========================================
# Hybrid Retrieval Core
# ==========================================


async def hybrid_search(
    db: AsyncSession,
    user_id: str,
    query: str,
    top_k: int = 5,
    include_vector: bool = True,
    include_keyword: bool = True,
    include_graph: bool = True,
) -> Dict[str, Any]:
    """
    Perform hybrid search across all memory types.
    
    Returns dict with:
        - vector_results: List of semantic memories (vector similarity)
        - keyword_results: List of episodic memories (keyword match)
        - graph_results: List of knowledge graph nodes (traversal)
        - combined: De-duplicated combined results with scores
    """
    results = {
        "vector_results": [],
        "keyword_results": [],
        "graph_results": [],
        "combined": [],
    }
    
    # 1. Vector similarity search (semantic memories)
    if include_vector:
        try:
            results["vector_results"] = await vector_search(db, user_id, query, top_k)
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
    
    # 2. Keyword search (episodic memories)
    if include_keyword:
        try:
            results["keyword_results"] = await keyword_search(db, user_id, query, top_k)
        except Exception as e:
            logger.warning(f"Keyword search failed: {e}")
    
    # 3. Graph traversal (knowledge graph)
    if include_graph:
        try:
            results["graph_results"] = await graph_search(db, user_id, query, top_k)
        except Exception as e:
            logger.warning(f"Graph search failed: {e}")
    
    # 4. Combine and de-duplicate
    results["combined"] = combine_and_deduplicate(
        results["vector_results"],
        results["keyword_results"],
        results["graph_results"],
    )
    
    return results


async def vector_search(
    db: AsyncSession,
    user_id: str,
    query: str,
    k: int = 5,
) -> List[Dict[str, Any]]:
    """Search semantic memories using vector similarity."""
    try:
        query_vector = embedder.embed(query)
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return []
    
    sql = text("""
        SELECT id, summary, content_preview, metadata,
               1 - (vector <=> :query_vec::vector) AS similarity
        FROM semantic_memory
        WHERE user_id = :uid AND index_semantic = TRUE
        ORDER BY vector <=> :query_vec::vector
        LIMIT :k
    """)
    
    result = await db.execute(
        sql, {"query_vec": str(query_vector), "uid": user_id, "k": k}
    )
    rows = result.fetchall()
    
    return [
        {
            "id": str(row[0]),
            "type": "semantic",
            "summary": row[1],
            "content_preview": row[2],
            "metadata": row[3] or {},
            "score": float(row[4]) if row[4] else 0.0,
            "source": "vector",
        }
        for row in rows
    ]


async def keyword_search(
    db: AsyncSession,
    user_id: str,
    query: str,
    k: int = 5,
) -> List[Dict[str, Any]]:
    """Search episodic memories using keyword matching."""
    # Extract keywords from query
    keywords = [w.strip() for w in query.split() if len(w.strip()) > 3]
    
    if not keywords:
        return []
    
    # Build ILIKE conditions for each keyword
    conditions = []
    params = {"uid": user_id, "k": k}
    
    for i, kw in enumerate(keywords[:5]):  # Limit to 5 keywords
        param_name = f"kw{i}"
        conditions.append(f"content ILIKE :{param_name}")
        params[param_name] = f"%{kw}%"
    
    where_clause = " OR ".join(conditions)
    
    sql = text(f"""
        SELECT id, content, timestamp, metadata, tags,
               ts_rank_cd(to_tsvector('english', content), 
                          plainto_tsquery('english', :query)) AS rank
        FROM episodic_memory
        WHERE user_id = :uid AND store_episodic = TRUE
          AND ({where_clause})
        ORDER BY rank DESC, timestamp DESC
        LIMIT :k
    """)
    
    params["query"] = query
    
    try:
        result = await db.execute(sql, params)
        rows = result.fetchall()
        
        return [
            {
                "id": str(row[0]),
                "type": "episodic",
                "content": row[1][:200],
                "timestamp": row[2].isoformat() if row[2] else None,
                "metadata": row[3] or {},
                "tags": row[4] or [],
                "score": float(row[5]) if row[5] else 0.5,
                "source": "keyword",
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning(f"Full-text search failed, falling back to ILIKE: {e}")
        # Fallback to simple ILIKE
        return await keyword_search_simple(db, user_id, query, k)


async def keyword_search_simple(
    db: AsyncSession,
    user_id: str,
    query: str,
    k: int = 5,
) -> List[Dict[str, Any]]:
    """Simple keyword search using ILIKE (fallback)."""
    keywords = [w.strip() for w in query.split() if len(w.strip()) > 3]
    
    if not keywords:
        return []
    
    conditions = []
    params = {"uid": user_id, "k": k}
    
    for i, kw in enumerate(keywords[:5]):
        param_name = f"kw{i}"
        conditions.append(f"content ILIKE :{param_name}")
        params[param_name] = f"%{kw}%"
    
    where_clause = " OR ".join(conditions)
    
    sql = text(f"""
        SELECT id, content, timestamp, metadata, tags
        FROM episodic_memory
        WHERE user_id = :uid AND store_episodic = TRUE
          AND ({where_clause})
        ORDER BY timestamp DESC
        LIMIT :k
    """)
    
    result = await db.execute(sql, params)
    rows = result.fetchall()
    
    return [
        {
            "id": str(row[0]),
            "type": "episodic",
            "content": row[1][:200],
            "timestamp": row[2].isoformat() if row[2] else None,
            "metadata": row[3] or {},
            "tags": row[4] or [],
            "score": 0.5,  # Default score for simple search
            "source": "keyword",
        }
        for row in rows
    ]


async def graph_search(
    db: AsyncSession,
    user_id: str,
    query: str,
    k: int = 5,
) -> List[Dict[str, Any]]:
    """Search knowledge graph using query terms and traversal."""
    # Extract potential entity names from query
    query_terms = [w.strip().lower() for w in query.split() if len(w.strip()) > 2]
    
    if not query_terms:
        return []
    
    # 1. Find nodes with matching labels
    result = await db.execute(
        select(KnowledgeNode)
        .where(KnowledgeNode.user_id == user_id)
        .where(KnowledgeNode.store_associative == True)
        .where(
            func.lower(KnowledgeNode.label).in_(query_terms)
            | func.lower(KnowledgeNode.label).like(f"%{query_terms[0]}%")
        )
        .limit(k)
    )
    matching_nodes = result.scalars().all()
    
    # 2. Traverse edges to find connected nodes (1-hop)
    node_ids = [str(n.id) for n in matching_nodes]
    graph_results = []
    
    for node in matching_nodes:
        graph_results.append({
            "id": str(node.id),
            "type": "graph_node",
            "label": node.label,
            "node_type": node.type,
            "properties": node.properties or {},
            "score": 0.8,  # Direct match gets high score
            "source": "graph",
        })
    
    if node_ids:
        # Get edges from matching nodes
        edge_result = await db.execute(
            select(KnowledgeEdge)
            .where(KnowledgeEdge.user_id == user_id)
            .where(KnowledgeEdge.from_node_id.in_(node_ids))
            .limit(20)
        )
        edges = edge_result.scalars().all()
        
        # Add connected nodes
        connected_node_ids = list(set(str(e.to_node_id) for e in edges if str(e.to_node_id) not in node_ids))
        
        if connected_node_ids:
            connected_result = await db.execute(
                select(KnowledgeNode).where(
                    KnowledgeNode.id.in_([uuid.UUID(nid) for nid in connected_node_ids])
                ).limit(k - len(graph_results))
            )
            for node in connected_result.scalars().all():
                graph_results.append({
                    "id": str(node.id),
                    "type": "graph_node",
                    "label": node.label,
                    "node_type": node.type,
                    "properties": node.properties or {},
                    "score": 0.4,  # Connected node gets lower score
                    "source": "graph",
                })
    
    return graph_results[:k]


def combine_and_deduplicate(
    vector_results: List[Dict[str, Any]],
    keyword_results: List[Dict[str, Any]],
    graph_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Combine results from all sources and de-duplicate.
    Assigns combined scores using weighted average.
    """
    # Use dict to track unique results by ID
    combined = {}
    
    # Weight for each source
    weights = {
        "vector": 0.5,
        "keyword": 0.3,
        "graph": 0.2,
    }
    
    # Process vector results
    for r in vector_results:
        rid = r["id"]
        if rid not in combined:
            combined[rid] = {
                **r,
                "sources": [r["source"]],
                "combined_score": r["score"] * weights.get(r["source"], 0.33),
            }
        else:
            # Update existing entry
            existing = combined[rid]
            existing["sources"].append(r["source"])
            existing["combined_score"] += r["score"] * weights.get(r["source"], 0.33)
    
    # Process keyword results
    for r in keyword_results:
        rid = r["id"]
        if rid not in combined:
            combined[rid] = {
                **r,
                "sources": [r["source"]],
                "combined_score": r["score"] * weights.get(r["source"], 0.33),
            }
        else:
            existing = combined[rid]
            existing["sources"].append(r["source"])
            existing["combined_score"] += r["score"] * weights.get(r["source"], 0.33)
    
    # Process graph results
    for r in graph_results:
        rid = r["id"]
        if rid not in combined:
            combined[rid] = {
                **r,
                "sources": [r["source"]],
                "combined_score": r["score"] * weights.get(r["source"], 0.33),
            }
        else:
            existing = combined[rid]
            existing["sources"].append(r["source"])
            existing["combined_score"] += r["score"] * weights.get(r["source"], 0.33)
    
    # Sort by combined score
    sorted_results = sorted(
        combined.values(),
        key=lambda x: x["combined_score"],
        reverse=True
    )
    
    return sorted_results


async def get_retrieval_context(
    db: AsyncSession,
    user_id: str,
    query: str,
    top_k: int = 5,
) -> Dict[str, Any]:
    """
    Get formatted context from hybrid retrieval for use in RAG.
    Returns episodic_context, semantic_context, and graph_context lists.
    """
    results = await hybrid_search(db, user_id, query, top_k)
    
    episodic_context = [
        r.get("content", "") for r in results["combined"]
        if r["type"] == "episodic"
    ]
    
    semantic_context = [
        r.get("summary", "") or r.get("content_preview", "")
        for r in results["combined"]
        if r["type"] == "semantic"
    ]
    
    graph_context = [
        f"{r.get('label', '')} ({r.get('node_type', '')}): {r.get('properties', {}).get('description', '')}"
        for r in results["combined"]
        if r["type"] == "graph_node"
    ]
    
    return {
        "episodic_context": episodic_context[:top_k],
        "semantic_context": semantic_context[:top_k],
        "graph_context": graph_context[:top_k],
        "raw_results": results,
    }
