"""Re-ranking Service - Uses local Cross-Encoder to re-rank retrieval results.

Phase 2: cross-encoder calls now go through the bounded ``reranker``
pool in app/core/concurrency.py so concurrent requests cannot spawn
unbounded executor threads.
"""

import logging
from typing import List, Dict, Any, Optional
import time

from app.config import settings
from app.core.concurrency import run_bounded

logger = logging.getLogger(__name__)

# Load Cross-Encoder model once (global)
_reranker = None
MODEL_NAME = 'cross-encoder/ms-marco-MiniLM-L-6-v2'

def get_reranker():
    """Lazy-load the Cross-Encoder model."""
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        t0 = time.perf_counter()
        logger.info(f"Loading Cross-Encoder model: {MODEL_NAME}")
        _reranker = CrossEncoder(MODEL_NAME)
        logger.info(
            "reranker.model_loaded",
            extra={"model": MODEL_NAME, "load_ms": round((time.perf_counter() - t0) * 1000, 1)},
        )
    return _reranker

# ==========================================
# Re-ranking Core
# ==========================================


async def rerank_results(
    query: str,
    results: List[Dict[str, Any]],
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """
    Re-rank retrieval results using local Cross-Encoder.
    
    Args:
        query: Original user query
        results: Combined results from hybrid search
        top_n: Number of top results to return
        
    Returns:
        Re-ranked list with updated 'rerank_score' field
    """
    if not results or len(results) <= 1:
        return results[:top_n]
    
    # Prepare pairs for Cross-Encoder: [query, document_text]
    pairs = []
    for r in results:
        # Extract text based on type
        if r.get("type") == "semantic":
            text = r.get("summary", "") or r.get("content_preview", "")
        elif r.get("type") == "episodic":
            text = r.get("content", "")
        elif r.get("type") == "graph_node":
            text = f"{r.get('label', '')} {r.get('properties', {}).get('description', '')}"
        else:
            text = r.get("content", "") or r.get("summary", "")
        
        pairs.append([query, text[:500]])  # Truncate to 500 chars
    
    # Get scores from Cross-Encoder via the bounded reranker pool.
    try:
        reranker = get_reranker()
        scores = await run_bounded("reranker", reranker.predict, pairs)

        # Add scores to results
        for i, r in enumerate(results):
            r["rerank_score"] = float(scores[i]) if i < len(scores) else 0.0

        # Sort by rerank_score descending
        results.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)
        return results[:top_n]
        
    except Exception as e:
        logger.error(f"Cross-Encoder re-ranking failed: {e}. Using RRF scores.")
        # Fallback to RRF score
        results.sort(key=lambda x: x.get("rrf_score", 0.0), reverse=True)
        return results[:top_n]


async def get_top_context(
    query: str,
    combined_results: List[Dict[str, Any]],
    top_k: int = 5,
) -> Dict[str, Any]:
    """
    Get top re-ranked context for RAG.
    Returns formatted episodic_context, semantic_context, graph_context.
    """
    # Re-rank results
    reranked = await rerank_results(query, combined_results, top_k)
    
    # Format context by type
    episodic_context = []
    semantic_context = []
    graph_context = []
    
    for r in reranked:
        if r.get("type") == "episodic":
            episodic_context.append(r.get("content", "")[:200])
        elif r.get("type") == "semantic":
            semantic_context.append(
                r.get("summary", "") or r.get("content_preview", "")
            )
        elif r.get("type") == "graph_node":
            graph_context.append(
                f"{r.get('label', '')} ({r.get('node_type', '')})"
            )
    
    return {
        "episodic_context": episodic_context,
        "semantic_context": semantic_context,
        "graph_context": graph_context,
        "reranked_results": reranked,
    }
