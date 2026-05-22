"""Consolidation Engine — turns episodic memories into semantic + graph rows.

Mimics human memory consolidation (short-term → long-term).
Uses an LLM (gpt-4o-mini) for high-signal summarisation.

Phase 6 hardening (P6-D6):
* The expensive precompute (LLM summary, NLP entity/action extraction,
  embedding) runs OUTSIDE any open Postgres transaction. Previously
  the SELECT in ``get_episodes_to_consolidate`` autobegan a
  transaction that stayed open across the LLM call — a 30 s hang on
  OpenAI would pin a Supabase pooler connection for 30 s.
* The session is committed/closed after the read, then re-opened
  for each per-episode write block (a single ``async with db.begin()``).
  This mirrors the pattern P2-S4 introduced for ``/memory/episode/write``.
* Each write block is one transaction: a mid-chain failure rolls
  back the whole episode atomically — we never observe a semantic
  row without a matching ``consolidated=true`` flag, or vice versa.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.concurrency import run_bounded
from app.models.memory import (
    EpisodicMemory,
    KnowledgeEdge,
    KnowledgeNode,
    SemanticMemory,
)

logger = logging.getLogger(__name__)


# ── Transport-layer DTO ──────────────────────────────────────────────────────
# We snapshot the minimal episode fields out of the read transaction
# so the precompute pass does not depend on an attached ORM object.
@dataclass(frozen=True)
class _EpisodeSnapshot:
    id: uuid.UUID
    user_id: str
    app_id: Optional[str]
    content: str
    tags: tuple


@dataclass(frozen=True)
class _PrecomputedEpisode:
    """Everything we computed *outside* the write transaction."""

    snapshot: _EpisodeSnapshot
    summary: str
    entities: List[str]
    actions: List[str]
    importance: float
    embedding: List[float]


# ── Helpers ──────────────────────────────────────────────────────────────────
async def get_episodes_to_consolidate(
    db: AsyncSession,
    user_id: str,
    days_old: int = 1,
) -> List[EpisodicMemory]:
    """Query unconsolidated episodes older than ``days_old`` days."""
    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    result = await db.execute(
        select(EpisodicMemory)
        .where(EpisodicMemory.user_id == user_id)
        .where(EpisodicMemory.consolidated.is_(False))
        .where(EpisodicMemory.store_episodic.is_(True))
        .where(EpisodicMemory.timestamp < cutoff_date)
        .order_by(EpisodicMemory.timestamp.asc())
    )
    return list(result.scalars().all())


async def summarize_with_llm(
    content: str,
    llm_service,
    max_length: int = 200,
) -> str:
    """Use gpt-4o-mini to create a concise summary.

    Retries are scoped to OpenAI's transient errors. On final failure
    we fall back to truncated raw content so consolidation can still
    succeed at lower fidelity.
    """
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )
    import openai

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(
            (
                openai.APIConnectionError,
                openai.RateLimitError,
                openai.InternalServerError,
            )
        ),
        reraise=True,
    )
    def _call_llm():
        return llm_service.client.chat.completions.create(
            model=settings.consolidation_llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the following memory concisely, extracting key "
                        "facts and insights. Keep under 200 words."
                    ),
                },
                {"role": "user", "content": content},
            ],
            temperature=0.3,
            max_tokens=300,
        )

    try:
        response = await asyncio.to_thread(_call_llm)
        summary = response.choices[0].message.content.strip()
        return summary[:max_length] if len(summary) > max_length else summary
    except Exception as exc:
        logger.warning(
            "consolidation: LLM summary failed after retries (%s); using raw content",
            exc,
        )
        return content[:max_length]


def extract_entities_and_actions_sync(
    content: str,
    engram_processor,
) -> Dict[str, Any]:
    """Synchronous NLP extraction using the engram processor."""
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
    except Exception as exc:
        logger.warning("consolidation: NLP extraction failed: %s", exc)
        return {
            "entities": [],
            "actions": [],
            "negated_actions": [],
            "objects": [],
        }


async def extract_entities_and_actions(
    content: str,
    engram_processor,
) -> Dict[str, Any]:
    """Async wrapper for NLP extraction under the bounded ``nlp`` pool."""
    return await run_bounded(
        "nlp", extract_entities_and_actions_sync, content, engram_processor
    )


def calculate_importance_score(
    content: str,
    tags: tuple,
    entities: List[str],
    actions: List[str],
) -> float:
    """Compute an importance score in [0, 1].

    Pure function — runs outside any DB transaction.
    """
    score = 0.0
    score += min(len(content.split()) / 100, 1.0)
    score += len(entities) * 0.2
    score += len(actions) * 0.1
    if tags:
        score += len(tags) * 0.15
    return min(score, 1.0)


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
    """Persist a graph edge created in NetworkX to ``knowledge_edges``."""
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


# ── Phase 1: pre-compute (NO transaction held) ───────────────────────────────
async def _precompute_episode(
    snapshot: _EpisodeSnapshot,
    embedder,
    llm_service,
    engram_processor,
) -> _PrecomputedEpisode:
    """Run every expensive non-DB step OUTSIDE any transaction.

    Order does not matter much; we keep them serial so we don't fan
    out unbounded OpenAI requests per episode. The bounded
    semaphore pools in ``app.core.concurrency`` limit the executor
    threads spinning behind ``embedder``/``nlp``.
    """
    summary = await summarize_with_llm(snapshot.content, llm_service)
    nlp_data = await extract_entities_and_actions(
        snapshot.content, engram_processor
    )
    entities = list(nlp_data.get("entities") or [])
    actions = list(nlp_data.get("actions") or [])
    embedding = await embedder.embed(summary)
    importance = calculate_importance_score(
        snapshot.content, snapshot.tags, entities, actions
    )
    return _PrecomputedEpisode(
        snapshot=snapshot,
        summary=summary,
        entities=entities,
        actions=actions,
        importance=importance,
        embedding=embedding,
    )


# ── Phase 2: write (one transaction per episode) ─────────────────────────────
async def _write_consolidated(
    db: AsyncSession,
    pre: _PrecomputedEpisode,
) -> bool:
    """Apply the pre-computed semantic + graph rows in one transaction.

    A failure here rolls back ALL of: the semantic insert, the graph
    nodes, and the episode-flag update. The next consolidation run
    will pick up the same episode again because ``consolidated``
    stays False.
    """
    snap = pre.snapshot
    try:
        async with db.begin():
            semantic = SemanticMemory(
                user_id=snap.user_id,
                episodic_id=snap.id,
                vector=pre.embedding,
                embedding_model="all-MiniLM-L6-v2",
                summary=pre.summary,
                content_preview=snap.content[:500],
                index_semantic=True,
            )
            db.add(semantic)

            for entity in pre.entities[:10]:
                db.add(
                    KnowledgeNode(
                        user_id=snap.user_id,
                        label=entity,
                        type="entity",
                        properties={"source": "consolidation"},
                        store_associative=True,
                    )
                )
            for action in pre.actions[:10]:
                db.add(
                    KnowledgeNode(
                        user_id=snap.user_id,
                        label=action,
                        type="action",
                        properties={"source": "consolidation"},
                        store_associative=True,
                    )
                )

            await db.execute(
                update(EpisodicMemory)
                .where(EpisodicMemory.id == snap.id)
                .values(
                    consolidated=True,
                    consolidated_at=datetime.utcnow(),
                    importance_score=pre.importance,
                )
            )
        logger.info(
            "consolidation: wrote episode %s for user %s",
            snap.id,
            snap.user_id,
        )
        return True
    except Exception as exc:
        # ``async with db.begin():`` already rolled back.
        logger.error(
            "consolidation: write failed for episode %s: %s", snap.id, exc
        )
        return False


# ── Public API ───────────────────────────────────────────────────────────────
async def consolidate_episode(
    db: AsyncSession,
    episode: EpisodicMemory,
    embedder,
    llm_service,
    engram_processor,
) -> bool:
    """Consolidate a single attached ORM episode (legacy entry point).

    Kept for callers (e.g. on-demand routes / tests) that already hold
    an attached object. The Celery path uses
    ``consolidate_for_user`` which snapshots the data first.
    """
    snapshot = _EpisodeSnapshot(
        id=episode.id,
        user_id=str(episode.user_id),
        app_id=episode.app_id,
        content=episode.content,
        tags=tuple(episode.tags or []),
    )
    # Make sure no transaction is held while we precompute.
    if db.in_transaction():
        await db.commit()
    pre = await _precompute_episode(
        snapshot, embedder, llm_service, engram_processor
    )
    return await _write_consolidated(db, pre)


async def consolidate_for_user(
    db: AsyncSession,
    user_id: str,
    embedder,
    llm_service,
    engram_processor,
    days_old: int = 1,
) -> int:
    """Run consolidation for one user. Returns count of consolidated episodes.

    Three-phase contract:
      1. Read episodes (autobegin tx, then commit so no tx is held).
      2. For each episode: precompute LLM/NLP/embedding *with no
         transaction held*.
      3. For each episode: open ONE transaction for the writes and
         the ``consolidated=True`` flag flip.
    """
    episodes = await get_episodes_to_consolidate(db, user_id, days_old)
    snapshots = [
        _EpisodeSnapshot(
            id=ep.id,
            user_id=str(ep.user_id),
            app_id=ep.app_id,
            content=ep.content,
            tags=tuple(ep.tags or []),
        )
        for ep in episodes
    ]
    # Close the read transaction so nothing is held during precompute.
    if db.in_transaction():
        await db.commit()

    if not snapshots:
        logger.info("consolidation: no episodes to consolidate for user %s", user_id)
        return 0

    consolidated_count = 0
    for snap in snapshots:
        pre = await _precompute_episode(
            snap, embedder, llm_service, engram_processor
        )
        success = await _write_consolidated(db, pre)
        if success:
            consolidated_count += 1

    logger.info(
        "consolidation: %s/%s episodes consolidated for user %s",
        consolidated_count,
        len(snapshots),
        user_id,
    )
    return consolidated_count


async def run_consolidation_all(
    db: AsyncSession,
    embedder,
    llm_service,
    engram_processor,
) -> Dict[str, Any]:
    """Run consolidation for every user with unconsolidated episodes.

    Used by the legacy in-process scheduler. The Celery path enqueues
    one task per user instead so each runs under its own RLS context
    and idempotency lock.
    """
    from sqlalchemy import distinct

    result = await db.execute(
        select(distinct(EpisodicMemory.user_id)).where(
            EpisodicMemory.consolidated.is_(False)
        )
    )
    user_ids = [row[0] for row in result.fetchall()]
    # Close the read transaction before iterating.
    if db.in_transaction():
        await db.commit()

    total_consolidated = 0
    for uid in user_ids:
        count = await consolidate_for_user(
            db, str(uid), embedder, llm_service, engram_processor
        )
        total_consolidated += count

    return {
        "status": "success",
        "total_consolidated": total_consolidated,
        "users_processed": len(user_ids),
    }
