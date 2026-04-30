"""Consolidation Engine - Converts episodic memories to semantic/graph memories.

Mimics human memory consolidation (short-term -> long-term).
Uses LLM summarization (gpt-4o-mini) for high-signal semantic memories.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import (
    EpisodicMemory,
    SemanticMemory,
    KnowledgeEdge,
    KnowledgeNode,
)
from app.config import settings
from app.services.engram_processor import NLP_SEMAPHORE

logger = logging.getLogger(__name__)

# ==========================================
# Core Consolidation Functions
# ==========================================


async def persist_edge(
    db: AsyncSession,
    source_id: str,
    target_id: str,
    relation: str,
    weight: float,
    user_id: str,
    app_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> KnowledgeEdge:
    """Persist a graph edge created in NetworkX to knowledge_edges."""
    edge = KnowledgeEdge(
        user_id=user_id,
        app_id=app_id,
        from_node_id=source_id,
        to_node_id=target_id,
        relation=relation,
        weight=weight,
        extra_metadata=metadata or {},
    )
    db.add(edge)
    await db.flush()
    return edge

async def get_episodes_to_consolidate(
    db: AsyncSession,
    user_id: str,
    days_old: int = 1,
) -> List[EpisodicMemory]:
    """Query unconsolidated episodes older than X days."""
    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    
    result = await db.execute(
        select(EpisodicMemory)
        .where(EpisodicMemory.user_id == user_id)
        .where(EpisodicMemory.consolidated == False)
        .where(EpisodicMemory.store_episodic == True)
        .where(EpisodicMemory.timestamp < cutoff_date)
        .order_by(EpisodicMemory.timestamp.asc())
    )
    return result.scalars().all()


async def summarize_with_llm(
    content: str,
    llm_service,
    max_length: int = 200,
) -> str:
    """Use gpt-4o-mini to create concise summary."""
    try:
        response = await asyncio.to_thread(
            llm_service.client.chat.completions.create,
            model=settings.consolidation_llm_model,
            messages=[
                {"role": "system", "content": "Summarize the following memory concisely, extracting key facts and insights. Keep under 200 words."},
                {"role": "user", "content": content},
            ],
            temperature=0.3,
            max_tokens=300,
        )
        summary = response.choices[0].message.content.strip()
        return summary[:max_length] if len(summary) > max_length else summary
    except Exception as e:
        logger.warning(f"LLM summarization failed: {e}. Using raw content.")
        return content[:max_length]


def extract_entities_and_actions_sync(
    content: str,
    engram_processor,
) -> Dict[str, Any]:
    """Synchronous NLP extraction using engram_processor."""
    try:
        doc = engram_processor._nlp(content)
        entities = engram_processor._extract_entities(doc)
        actions, negated_actions = engram_processor._extract_actions(doc)
        objects = engram_processor._extract_objects(doc)
        
        return {
            "entities": entities,
            "actions": actions,
            "negated_actions": negated_actions,
            "objects": objects,
        }
    except Exception as e:
        logger.warning(f"NLP extraction failed: {e}")
        return {"entities": [], "actions": [], "negated_actions": [], "objects": []}


async def extract_entities_and_actions(
    content: str,
    engram_processor,
) -> Dict[str, Any]:
    """Async wrapper for NLP extraction with semaphore."""
    loop = asyncio.get_event_loop()
    async with NLP_SEMAPHORE:
        return await loop.run_in_executor(
            None, extract_entities_and_actions_sync, content, engram_processor
        )


async def create_semantic_from_episode(
    db: AsyncSession,
    episode: EpisodicMemory,
    summary: str,
    embedder,
) -> Optional[SemanticMemory]:
    """Create semantic memory from consolidated episode."""
    try:
        # Generate embedding for the summary
        vector = await embedder.embed(summary)
        
        semantic = SemanticMemory(
            user_id=str(episode.user_id),
            episodic_id=episode.id,
            vector=vector,
            embedding_model="all-MiniLM-L6-v2",
            summary=summary,
            content_preview=episode.content[:500],
            index_semantic=True,
        )
        db.add(semantic)
        await db.flush()
        return semantic
    except Exception as e:
        logger.error(f"Failed to create semantic memory: {e}")
        return None


async def create_graph_nodes_from_entities(
    db: AsyncSession,
    user_id: str,
    entities: List[str],
    actions: List[str],
) -> Dict[str, str]:
    """Create knowledge graph nodes from extracted entities. Returns node_id mapping."""
    node_mapping = {}
    
    # Create nodes for entities
    for entity in entities[:10]:  # Limit to top 10
        try:
            node = KnowledgeNode(
                user_id=user_id,
                label=entity,
                type="entity",
                properties={"source": "consolidation"},
                store_associative=True,
            )
            db.add(node)
            await db.flush()
            node_mapping[entity] = str(node.id)
        except Exception as e:
            logger.warning(f"Failed to create node for entity {entity}: {e}")
    
    # Create nodes for actions
    for action in actions[:10]:  # Limit to top 10
        try:
            node = KnowledgeNode(
                user_id=user_id,
                label=action,
                type="action",
                properties={"source": "consolidation"},
                store_associative=True,
            )
            db.add(node)
            await db.flush()
            node_mapping[action] = str(node.id)
        except Exception as e:
            logger.warning(f"Failed to create node for action {action}: {e}")
    
    return node_mapping


async def link_related_episodes(
    db: AsyncSession,
    episode: EpisodicMemory,
    entities: List[str],
    engram_processor,
) -> int:
    """Create graph edges between related episodes based on shared entities."""
    from app.models.memory import KnowledgeEdge
    
    # Find other recent episodes with shared entities
    result = await db.execute(
        select(EpisodicMemory)
        .where(EpisodicMemory.user_id == str(episode.user_id))
        .where(EpisodicMemory.id != episode.id)
        .where(EpisodicMemory.consolidated == True)
        .order_by(EpisodicMemory.timestamp.desc())
        .limit(5)
    )
    related_episodes = result.scalars().all()
    
    edges_created = 0
    
    # This is a simplified version - in production, you'd want more sophisticated matching
    # For now, we'll create edges between episodes that share entities
    # (This would require storing entity lists with episodes - a future enhancement)
    
    return edges_created


async def calculate_importance_score(
    episode: EpisodicMemory,
    entities: List[str],
    actions: List[str],
) -> float:
    """Calculate importance score based on entities, actions, and content length."""
    score = 0.0
    
    # Base score from content length
    score += min(len(episode.content.split()) / 100, 1.0)
    
    # Bonus for entities (named entities are important)
    score += len(entities) * 0.2
    
    # Bonus for actions
    score += len(actions) * 0.1
    
    # Bonus for tags
    if episode.tags:
        score += len(episode.tags) * 0.15
    
    return min(score, 1.0)


async def consolidate_episode(
    db: AsyncSession,
    episode: EpisodicMemory,
    embedder,
    llm_service,
    engram_processor,
) -> bool:
    """Process single episode - create semantic memory and graph nodes."""
    try:
        # 1. Summarize with LLM
        summary = await summarize_with_llm(episode.content, llm_service)
        
        # 2. Extract entities and actions
        nlp_data = await extract_entities_and_actions(episode.content, engram_processor)
        entities = nlp_data["entities"]
        actions = nlp_data["actions"]
        
        # 3. Create semantic memory
        semantic = await create_semantic_from_episode(
            db, episode, summary, embedder
        )
        
        # 4. Create graph nodes from entities
        node_mapping = await create_graph_nodes_from_entities(
            db, str(episode.user_id), entities, actions
        )
        
        # 5. Calculate importance score
        importance = await calculate_importance_score(episode, entities, actions)
        
        # 6. Link related episodes (simplified for now)
        await link_related_episodes(db, episode, entities, engram_processor)
        
        # 7. Mark episode as consolidated
        episode.consolidated = True
        episode.consolidated_at = datetime.utcnow()
        episode.importance_score = importance
        
        await db.commit()
        logger.info(f"Consolidated episode {episode.id}: {summary[:50]}...")
        return True
        
    except Exception as e:
        logger.error(f"Failed to consolidate episode {episode.id}: {e}")
        await db.rollback()
        return False


async def consolidate_for_user(
    db: AsyncSession,
    user_id: str,
    embedder,
    llm_service,
    engram_processor,
    days_old: int = 1,
) -> int:
    """Run consolidation for one user. Returns count of consolidated episodes."""
    episodes = await get_episodes_to_consolidate(db, user_id, days_old)
    
    if not episodes:
        logger.info(f"No episodes to consolidate for user {user_id}")
        return 0
    
    consolidated_count = 0
    for episode in episodes:
        success = await consolidate_episode(
            db, episode, embedder, llm_service, engram_processor
        )
        if success:
            consolidated_count += 1
    
    logger.info(f"Consolidated {consolidated_count} episodes for user {user_id}")
    return consolidated_count


async def run_consolidation_all(
    db: AsyncSession,
    embedder,
    llm_service,
    engram_processor,
) -> Dict[str, Any]:
    """Run consolidation for all users. Called by scheduler."""
    from sqlalchemy import distinct
    
    # Get all unique user_ids with unconsolidated episodes
    result = await db.execute(
        select(distinct(EpisodicMemory.user_id))
        .where(EpisodicMemory.consolidated == False)
    )
    user_ids = [row[0] for row in result.fetchall()]
    
    total_consolidated = 0
    for user_id in user_ids:
        count = await consolidate_for_user(
            db, str(user_id), embedder, llm_service, engram_processor
        )
        total_consolidated += count
    
    return {
        "status": "success",
        "total_consolidated": total_consolidated,
        "users_processed": len(user_ids),
    }
