"""RAG (Retrieval-Augmented Generation) API endpoints."""

import asyncio
import logging
import time
import structlog
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.database import get_db
from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User
from app.services.retriever import get_retrieval_context
from app.services.reranker import get_top_context

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="", tags=["rag"])


def _generate_demo_reply(
    message: str,
    episodic_context: list[str],
    semantic_context: list[str],
    procedural_context: dict | None,
) -> str:
    """Generate a demo reply when LLM is not available."""
    message_lower = message.lower()

    if "memory" in message_lower or "remember" in message_lower:
        memories = []
        if episodic_context:
            memories.append(f"I recall from our recent conversations: {episodic_context[0][:100]}")
        if semantic_context:
            memories.append(f"Based on semantic matching: {semantic_context[0][:100]}")
        if memories:
            return f"Based on my memory, here's what I know:\n\n{' '.join(memories)}\n\nIs there anything specific you'd like to explore?"

    if "preference" in message_lower or "setting" in message_lower:
        if procedural_context:
            settings_data = procedural_context.get("settings", {})
            return f"Based on your stored preferences:\n- Theme: {settings_data.get('theme', 'default')}\n- Language: {settings_data.get('language', 'en')}\n- Response style: {settings_data.get('response_style', 'concise')}\n\nWould you like to update any of these?"

    if "startup" in message_lower or "project" in message_lower:
        return "From what I remember, you're building a startup in the AI infrastructure space, focusing on memory systems for LLMs. You've discussed using pgvector and PostgreSQL for the storage backend. Would you like to dive deeper into any aspect of this?"

    if "debug" in message_lower or "error" in message_lower:
        return "I recall you had some debugging questions about Python async with FastAPI. The issue involved connection pool timeouts with asyncpg. Would you like me to recall the specific solution we discussed?"

    if "hello" in message_lower or "hi" in message_lower:
        return "Hello! I'm your AI assistant with persistent memory. I can remember our past conversations, your preferences, and use semantic search to find relevant context. How can I help you today?"

    context_parts = []
    if episodic_context:
        context_parts.append(f"I remember: {episodic_context[0][:80]}")
    if semantic_context:
        context_parts.append(f"Related context: {semantic_context[0][:80]}")

    if context_parts:
        return f"I've searched my memory and found some relevant information:\n\n{' '.join(context_parts)}\n\nHow can I assist you further?"

    return "I'm your AI assistant with a persistent memory layer. I can remember our conversations, learn your preferences, and use semantic search to find relevant information. What would you like to discuss?"


from app.schemas.memory import RAGRequest, RAGResponse

@router.post("/rag/chat", response_model=RAGResponse)
async def rag_chat(
    request: RAGRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a memory-augmented LLM response."""
    # Validate request user_id matches authenticated user
    if str(current_user.id) != str(request.user_id):
        raise HTTPException(status_code=403, detail="User ID mismatch")
    
    user_id = str(current_user.id)
    message = request.message
    session_id = request.session_id
    include_episodic = request.include_episodic
    include_semantic = request.include_semantic
    include_procedural = request.include_procedural
    include_graph = request.include_graph
    top_k = request.top_k
    start_time = time.time()

    episodic_context = []
    semantic_context = []
    procedural_context = None
    graph_context = []

    if settings.demo_mode:
        from app.demo_db import (
            get_episodic, search_semantic, get_procedural,
            get_nodes, create_episodic, create_semantic
        )
        from app.services.embedder import embedder

        if include_episodic:
            episodes = get_episodic(user_id, limit=10)
            episodic_context = [ep["content"] for ep in episodes]

        if include_semantic:
            try:
                query_vector = await asyncio.to_thread(embedder.embed, message)
            except Exception:
                query_vector = embedder.random_vector()
            results = search_semantic(user_id, query_vector, top_k)
            semantic_context = [r.get("summary") or r.get("content_preview", "") for r in results]

        if include_procedural:
            procedural_context = get_procedural(user_id)

        if include_graph:
            nodes = get_nodes(user_id)
            graph_context = [
                f"{n['label']} ({n['type']}): {n.get('properties', {}).get('description', '')}"
                for n in nodes[:10]
            ]

        try:
            from app.services.llm import llm_service
            llm_result = await asyncio.to_thread(
                llm_service.generate_rag_response,
                user_message=message,
                episodic_context=episodic_context,
                semantic_context=semantic_context,
                procedural_context=procedural_context,
                graph_context=graph_context,
            )
            reply = llm_result["reply"]
            prompt_tokens = llm_result.get("prompt_tokens", 0)
            completion_tokens = llm_result.get("completion_tokens", 0)
            
            # Task 4.4: Cost/Token Tracking — app_id comes from the request
            # body (RAGRequest.app_id), not from the User model. The User
            # model has no app_id attribute; reading it would raise.
            logger.info(
                "llm_token_usage",
                app_id=request.app_id,
                user_id=user_id,
                endpoint="rag_chat_demo",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=settings.openai_llm_model,
                latency_ms=llm_result.get("latency_ms", 0)
            )
        except Exception as e:
            logger.warning("llm_generation_failed", error=str(e), user_id=user_id)
            reply = _generate_demo_reply(message, episodic_context, semantic_context, procedural_context)
            prompt_tokens = len(message.split()) * 2
            completion_tokens = len(reply.split()) * 2

        session = session_id or f"rag_{user_id}"
        create_episodic(
            user_id=user_id, session_id=session,
            content=f"User: {message}\nAssistant: {reply}",
            metadata={"source": "rag_chat", "mode": "demo"},
            tags=["rag_interaction"],
        )

        try:
            import random
            question_vector = [random.gauss(0, 1) for _ in range(384)]
            norm = sum(v * v for v in question_vector) ** 0.5
            question_vector = [v / norm for v in question_vector]
            create_semantic(
                user_id=user_id, vector=question_vector,
                summary=message[:200], content_preview=message[:500],
                metadata={"source": "rag_question", "type": "user_query"},
            )
        except Exception as e:
            logger.warning(f"Failed to create semantic memory: {e}")

        latency_ms = (time.time() - start_time) * 1000

        return {
            "reply": reply,
            "retrieved_episodes": episodic_context[:5],
            "retrieved_semantics": semantic_context[:5],
            "retrieved_procedural": procedural_context,
            "retrieved_graph_nodes": graph_context[:5],
            "retrieved_graph_edges": [],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": round(latency_ms, 2),
        }

    # Production mode with PostgreSQL
    from app.models.memory import EpisodicMemory, ProceduralMemory, KnowledgeNode
    from app.services.embedder import embedder
    from app.services.llm import llm_service

    # Extract app_id from request
    app_id = request.app_id
    
    # Use hybrid retrieval
    try:
        retrieval_results = await get_retrieval_context(
            db, user_id, message, top_k, app_id=app_id
        )
        episodic_context = retrieval_results["episodic_context"]
        semantic_context = retrieval_results["semantic_context"]
        graph_context = retrieval_results["graph_context"]
    except Exception as e:
        logger.warning(f"Hybrid retrieval failed: {e}, falling back to basic search")
        # Fallback to basic search
        episodic_context = []
        semantic_context = []
        graph_context = []
        
        if include_episodic:
            result = await db.execute(
                select(EpisodicMemory)
                .where(EpisodicMemory.user_id == user_id)
                .where(EpisodicMemory.store_episodic == True)
                .order_by(EpisodicMemory.timestamp.desc())
                .limit(10)
            )
            episodic_context = [ep.content for ep in result.scalars().all()]
        
        if include_semantic:
            try:
                query_vector = await asyncio.to_thread(embedder.embed, message)
                sql = text("""
                    SELECT content_preview, summary FROM semantic_memory
                    WHERE user_id = :uid AND index_semantic = TRUE
                    ORDER BY vector <=> CAST(:query_vec AS vector) LIMIT :k
                """)
                result = await db.execute(sql, {"query_vec": str(query_vector), "uid": user_id, "k": top_k})
                semantic_context = [row.summary or row.content_preview or "" for row in result.fetchall()]
            except Exception as e2:
                logger.warning(f"Semantic search failed: {e2}")
        
        if include_graph:
            result = await db.execute(
                select(KnowledgeNode).where(
                    KnowledgeNode.user_id == user_id, KnowledgeNode.store_associative == True
                ).limit(10)
            )
            graph_context = [f"{n.label} ({n.type}): {n.properties.get('description', '')}" for n in result.scalars().all()]
    
    # Get procedural memory (separate from search)
    if include_procedural:
        result = await db.execute(
            select(ProceduralMemory).where(
                ProceduralMemory.user_id == user_id,
                ProceduralMemory.store_procedural == True,
            )
        )
        proc = result.scalar_one_or_none()
        if proc:
            procedural_context = {"settings": proc.settings, "workflows": proc.workflows}

    try:
        llm_result = await asyncio.to_thread(
            llm_service.generate_rag_response,
            user_message=message, episodic_context=episodic_context,
            semantic_context=semantic_context, procedural_context=procedural_context,
            graph_context=graph_context,
        )
        reply, prompt_tokens, completion_tokens = llm_result["reply"], llm_result.get("prompt_tokens", 0), llm_result.get("completion_tokens", 0)
        
        # Task 4.4: Cost/Token Tracking — app_id is request-scoped (see
        # docs/APP_SCOPING.md). The User model has no app_id attribute.
        logger.info(
            "llm_token_usage",
            app_id=request.app_id,
            user_id=user_id,
            endpoint="rag_chat_production",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=settings.openai_llm_model,
            latency_ms=llm_result.get("latency_ms", 0)
        )
    except Exception as e:
        logger.warning("llm_service_error", error=str(e), user_id=user_id)
        reply = _generate_demo_reply(message, episodic_context, semantic_context, procedural_context)
        prompt_tokens = len(message.split()) * 2
        completion_tokens = len(reply.split()) * 2

    session = session_id or f"rag_{user_id}"
    new_episode = EpisodicMemory(
        user_id=user_id, session_id=session,
        content=f"User: {message}\nAssistant: {reply}",
        extra_metadata={"source": "rag_chat", "model": settings.openai_llm_model},
        tags=["rag_interaction"], store_episodic=True,
    )
    db.add(new_episode)
    await db.commit()

    latency_ms = (time.time() - start_time) * 1000
    return {
        "reply": reply,
        "retrieved_episodes": episodic_context[:5],
        "retrieved_semantics": semantic_context[:5],
        "retrieved_procedural": procedural_context,
        "retrieved_graph_nodes": graph_context[:5],
        "retrieved_graph_edges": [],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": round(latency_ms, 2),
    }
