"""Re-ranking Service - Uses LLM to re-rank retrieval results."""

import logging
from typing import List, Dict, Any, Optional
from app.config import settings
from app.services.llm import llm_service

logger = logging.getLogger(__name__)

# ==========================================
# Re-ranking Core
# ==========================================


async def rerank_results(
    query: str,
    results: List[Dict[str, Any]],
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """
    Re-rank retrieval results using LLM relevance scoring.
    
    Args:
        query: Original user query
        results: Combined results from hybrid search
        top_n: Number of top results to return
        
    Returns:
        Re-ranked list with updated 'rerank_score' field
    """
    if not results:
        return []
    
    # If too many results, use LLM to score them
    if len(results) > top_n:
        try:
            scored_results = await score_with_llm(query, results)
            # Sort by rerank_score descending
            scored_results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
            return scored_results[:top_n]
        except Exception as e:
            logger.warning(f"LLM re-ranking failed: {e}. Using original scores.")
            # Fallback to combined_score
            results.sort(key=lambda x: x.get("combined_score", 0), reverse=True)
            return results[:top_n]
    else:
        # Not too many results, just return sorted by combined_score
        results.sort(key=lambda x: x.get("combined_score", 0), reverse=True)
        return results[:top_n]


async def score_with_llm(
    query: str,
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Use LLM to score each result's relevance to the query.
    Returns results with added 'rerank_score' field (0-1).
    """
    # Build prompt with all results
    prompt = build_rerank_prompt(query, results)
    
    try:
        response = llm_service.client.chat.completions.create(
            model=settings.consolidation_llm_model,  # Use gpt-4o-mini for cost efficiency
            messages=[
                {"role": "system", "content": "You are a relevance scoring system. Score each result's relevance from 0.0 to 1.0."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        
        # Parse scores from LLM response
        response_text = response.choices[0].message.content
        scores = parse_rerank_response(response_text, len(results))
        
        # Add scores to results
        for i, result in enumerate(results):
            result["rerank_score"] = scores[i] if i < len(scores) else 0.0
            
        return results
        
    except Exception as e:
        logger.error(f"LLM scoring failed: {e}")
        raise


def build_rerank_prompt(query: str, results: List[Dict[str, Any]]) -> str:
    """Build prompt for LLM re-ranking."""
    parts = [
        f"Query: {query}",
        "",
        "Rate the relevance of each result to the query on a scale of 0.0 to 1.0.",
        "Return ONLY a comma-separated list of scores, one per result.",
        "",
        "Results:",
    ]
    
    for i, result in enumerate(results):
        content = ""
        if result.get("type") == "semantic":
            content = result.get("summary", "") or result.get("content_preview", "")
        elif result.get("type") == "episodic":
            content = result.get("content", "")
        elif result.get("type") == "graph_node":
            content = f"{result.get('label', '')}: {result.get('properties', {}).get('description', '')}"
        
        # Truncate content
        content = content[:200] if len(content) > 200 else content
        parts.append(f"{i+1}. {content}")
    
    parts.append("")
    parts.append("Scores (comma-separated, e.g., 0.9, 0.5, 0.2):")
    
    return "\n".join(parts)


def parse_rerank_response(response: str, expected_count: int) -> List[float]:
    """
    Parse LLM response to extract scores.
    Expected format: "0.9, 0.5, 0.2" or similar.
    """
    scores = []
    
    # Try to extract numbers from response
    import re
    # Find all decimal numbers in the response
    matches = re.findall(r"0?\.\d+|1\.0|0", response)
    
    for match in matches[:expected_count]:
        try:
            score = float(match)
            # Clamp to [0, 1]
            score = max(0.0, min(1.0, score))
            scores.append(score)
        except ValueError:
            scores.append(0.0)
    
    # Pad with zeros if not enough scores
    while len(scores) < expected_count:
        scores.append(0.0)
    
    return scores[:expected_count]


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
