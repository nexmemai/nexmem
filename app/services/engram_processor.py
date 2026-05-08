"""Engram Processor - Memory compression with spaCy + NetworkX.

Six fixes implemented:
1. Async wrapper (non-blocking event loop)
2. Real dense embeddings (sentence-transformers)
3. Weighted co-occurrence scoring
4. Negation detection
5. Salience scoring
6. Chunking for long input
"""

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from functools import partial
from typing import Dict, List, Optional, Any
import uuid

import networkx as nx

from app.config import settings

from app.services.embedder import get_nlp_semaphore


def _empty_user_context() -> Dict[str, Any]:
    return {
        "entities": [],
        "actions": [],
        "objects": [],
        "graph": nx.Graph(),
        "engrams": [],
    }


class EngramProcessor:
    """Processes text into compressed memory units (engrams)."""

    def __init__(self, preloaded_contexts: Optional[Dict[str, Dict]] = None):
        import spacy
        from sentence_transformers import SentenceTransformer

        self._nlp = spacy.load("en_core_web_sm")
        self._embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        self._graph = nx.Graph()
        self._user_contexts: Dict[str, Dict] = preloaded_contexts or {}

    def _chunk_text(self, text: str, max_tokens: int = 200) -> List[str]:
        """Split long text into overlapping chunks."""
        words = text.split()
        if len(words) <= max_tokens:
            return [text]

        step = max_tokens - 20
        chunks = []
        for i in range(0, len(words), step):
            chunk = " ".join(words[i:i + max_tokens])
            chunks.append(chunk)
        return chunks

    def _get_embedding(self, text: str) -> List[float]:
        """Get dense embedding using sentence-transformers."""
        return self._embed_model.encode(
            text, normalize_embeddings=True
        ).tolist()

    def _score_salience(self, token: str, doc) -> float:
        """Calculate salience score for a token."""
        score = 1.0

        if any(ent.text == token for ent in doc.ents):
            score += 2.0

        if any(ch.isdigit() for ch in token):
            score += 1.5

        if any(
            t.dep_ in ("nsubj", "dobj") and t.text == token
            for t in doc
        ):
            score += 1.0

        if len(token) <= 3:
            score -= 0.5

        return max(score, 0.1)

    def _extract_entities(self, doc) -> List[str]:
        """Extract named entities, excluding types we don't care about."""
        exclude_types = {"CARDINAL", "ORDINAL", "DATE", "TIME", "PERCENT", "MONEY"}
        return [
            ent.text.lower()
            for ent in doc.ents
            if ent.label_ not in exclude_types
        ]

    def _extract_objects(self, doc) -> List[str]:
        """Extract noun objects from the document."""
        objects = []
        for token in doc:
            if token.pos_ == "NOUN" and token.dep_ in ("dobj", "pobj", "attr"):
                if token.text.lower() not in objects:
                    objects.append(token.text.lower())
        return objects

    def _extract_actions(self, doc) -> List[str]:
        """Extract verbs with negation detection."""
        actions = []
        negated_actions = []

        for token in doc:
            if token.pos_ == "VERB":
                has_neg = any(child.dep_ == "neg" for child in token.children)
                key = f"NOT_{token.lemma_}" if has_neg else token.lemma_

                if has_neg:
                    negated_actions.append(key)
                else:
                    actions.append(key)

        return actions, negated_actions

    def _process_sync(self, text: str, user_id: str) -> Dict[str, Any]:
        """Synchronous processing - runs in thread pool."""
        chunks = self._chunk_text(text)
        all_entities = []
        all_actions = []
        all_objects = []
        all_negated_actions = []
        salience_scores = {}
        compressed_parts = []

        for chunk in chunks:
            doc = self._nlp(chunk)

            entities = self._extract_entities(doc)
            objects = self._extract_objects(doc)
            actions, negated = self._extract_actions(doc)

            all_entities.extend(entities)
            all_objects.extend(objects)
            all_actions.extend(actions)
            all_negated_actions.extend(negated)

            for token in doc:
                if token.pos_ in ("NOUN", "VERB", "PROPN"):
                    score = self._score_salience(token.text, doc)
                    if token.text.lower() not in salience_scores:
                        salience_scores[token.text.lower()] = score
                    else:
                        salience_scores[token.text.lower()] = max(
                            salience_scores[token.text.lower()], score
                        )

            compressed_parts.append(chunk[:100])

        all_entities = list(set(all_entities))
        all_objects = list(set(all_objects))
        all_actions = list(set(all_actions))
        all_negated_actions = list(set(all_negated_actions))

        distilled = "; ".join(compressed_parts[:3])
        if len(distilled) > 200:
            distilled = distilled[:200] + "..."

        original_length = len(text.split())
        compressed_length = len(distilled.split())
        compression_ratio = (
            1.0 - (compressed_length / original_length)
            if original_length > 0
            else 0.0
        )

        engram_id = hashlib.sha256(
            f"{user_id}:{text}".encode()
        ).hexdigest()[:12]

        embedding = self._get_embedding(distilled)

        user_key = str(user_id)
        if user_key not in self._user_contexts:
            self._user_contexts[user_key] = _empty_user_context()

        context = self._user_contexts[user_key]
        connections = []

        for entity in all_entities:
            if entity in context["entities"]:
                for other_engram in context["engrams"][-5:]:
                    if entity in other_engram.get("entities", []):
                        connections.append(other_engram["engram_id"])

        for action in all_actions:
            if action in context["actions"]:
                for other_engram in context["engrams"][-5:]:
                    if action in other_engram.get("actions", []):
                        connections.append(other_engram["engram_id"])

        for obj in all_objects:
            if obj in context["objects"]:
                for other_engram in context["engrams"][-5:]:
                    if obj in other_engram.get("objects", []):
                        connections.append(other_engram["engram_id"])

        connections = list(set(connections))

        engram = {
            "engram_id": engram_id,
            "distilled_text": distilled,
            "dense_embedding": embedding,
            "entities": all_entities,
            "objects": all_objects,
            "actions": all_actions,
            "negated_actions": all_negated_actions,
            "salience_scores": salience_scores,
            "connections": connections,
            "graph_edges": [],
            "original_length": original_length,
            "compressed_length": compressed_length,
            "compression_ratio": round(compression_ratio, 3),
            "created_at": datetime.now(timezone.utc),
        }

        context["entities"].extend(all_entities)
        context["objects"].extend(all_objects)
        context["actions"].extend(all_actions)
        context["engrams"].append(engram)

        for i, entity1 in enumerate(all_entities):
            for entity2 in all_entities[i + 1:]:
                if not context["graph"].has_edge(entity1, entity2):
                    context["graph"].add_edge(
                        entity1, entity2, weight=2.5, relation="co_occur"
                    )
                    engram["graph_edges"].append({
                        "source": entity1,
                        "source_type": "entity",
                        "target": entity2,
                        "target_type": "entity",
                        "relation": "co_occur",
                        "weight": 2.5,
                    })

        for action in all_actions:
            for obj in all_objects:
                if not context["graph"].has_edge(action, obj):
                    context["graph"].add_edge(
                        action, obj, weight=1.5, relation="action_object"
                    )
                    engram["graph_edges"].append({
                        "source": action,
                        "source_type": "action",
                        "target": obj,
                        "target_type": "object",
                        "relation": "action_object",
                        "weight": 1.5,
                    })

        for action in all_actions:
            for entity in all_entities:
                if not context["graph"].has_edge(action, entity):
                    context["graph"].add_edge(
                        action, entity, weight=1.0, relation="action_entity"
                    )
                    engram["graph_edges"].append({
                        "source": action,
                        "source_type": "action",
                        "target": entity,
                        "target_type": "entity",
                        "relation": "action_entity",
                        "weight": 1.0,
                    })

        for i, action1 in enumerate(all_actions):
            for action2 in all_actions[i + 1:]:
                if not context["graph"].has_edge(action1, action2):
                    context["graph"].add_edge(
                        action1, action2, weight=1.0, relation="co_action"
                    )
                    engram["graph_edges"].append({
                        "source": action1,
                        "source_type": "action",
                        "target": action2,
                        "target_type": "action",
                        "relation": "co_action",
                        "weight": 1.0,
                    })

        return engram

    def load_graph_edge(
        self,
        user_id: str,
        source: str,
        target: str,
        relation: str,
        weight: float,
        source_type: Optional[str] = None,
        target_type: Optional[str] = None,
    ) -> None:
        """Load a persisted edge into the in-memory NetworkX graph."""
        user_key = str(user_id)
        if user_key not in self._user_contexts:
            self._user_contexts[user_key] = _empty_user_context()

        context = self._user_contexts[user_key]
        context["graph"].add_edge(source, target, relation=relation, weight=weight)
        if source_type == "entity" and source not in context["entities"]:
            context["entities"].append(source)
        if target_type == "entity" and target not in context["entities"]:
            context["entities"].append(target)
        if source_type == "action" and source not in context["actions"]:
            context["actions"].append(source)
        if target_type == "action" and target not in context["actions"]:
            context["actions"].append(target)
        if source_type == "object" and source not in context["objects"]:
            context["objects"].append(source)
        if target_type == "object" and target not in context["objects"]:
            context["objects"].append(target)

    async def process_async(self, text: str, user_id: str) -> Dict[str, Any]:
        """Async wrapper - processes text without blocking the event loop."""
        loop = asyncio.get_running_loop()
        sem = get_nlp_semaphore()
        async with sem:
            return await loop.run_in_executor(
                None, partial(self._process_sync, text, user_id)
            )

    def get_compressed_context(self, query: str, user_id: str) -> str:
        """Get compressed context for a user based on their engram history."""
        user_key = str(user_id)
        if user_key not in self._user_contexts:
            return ""

        context = self._user_contexts[user_key]
        high_salience = [
            (entity, score)
            for entity, score in context.get("engrams", [{}])[-1]
            .get("salience_scores", {})
            .items()
            if score >= 2.0
        ]
        high_salience.sort(key=lambda x: x[1], reverse=True)

        entities = list(set(context.get("entities", [])))[:10]
        actions = list(set(context.get("actions", [])))[:10]

        parts = []
        if entities:
            parts.append(f"Known entities: {', '.join(entities)}")
        if actions:
            parts.append(f"Related actions: {', '.join(actions)}")
        if high_salience:
            parts.append(
                f"Important terms: {', '.join([e for e, _ in high_salience[:5]])}"
            )

        return "; ".join(parts) if parts else ""

    def get_graph_summary(self, user_id: str) -> Dict[str, Any]:
        """Get a summary of the user's knowledge graph."""
        user_key = str(user_id)
        if user_key not in self._user_contexts:
            return {"nodes": 0, "edges": 0, "density": 0.0}

        context = self._user_contexts[user_key]
        graph = context["graph"]

        return {
            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges(),
            "density": nx.density(graph) if graph.number_of_nodes() > 0 else 0.0,
            "degree_centrality": dict(
                nx.degree_centrality(graph)
            ) if graph.number_of_nodes() > 0 else {},
        }


def decay_score(created_at: datetime, half_life_days: float = 30.0) -> float:
    """Calculate temporal decay score for an engram."""
    import math

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    age = (datetime.now(timezone.utc) - created_at).days
    return math.exp(-0.693 * age / half_life_days)


class LazyEngramProcessor:
    """Create the heavy NLP models only when memory processing is first used."""

    def __init__(self):
        self._instance: Optional[EngramProcessor] = None
        self._lock: Optional[asyncio.Lock] = None
        self._preloaded_contexts: Dict[str, Dict] = {}

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _get(self) -> EngramProcessor:
        if self._instance is None:
            lock = self._get_lock()
            async with lock:
                if self._instance is None:
                    self._instance = EngramProcessor(self._preloaded_contexts)
        return self._instance

    async def process_async(self, text: str, user_id: str) -> Dict[str, Any]:
        processor = await self._get()
        return await processor.process_async(text, user_id)

    def get_compressed_context(self, query: str, user_id: str) -> str:
        return self._get_instance().get_compressed_context(query, user_id)

    def get_graph_summary(self, user_id: str) -> Dict[str, Any]:
        if self._instance is None:
            user_key = str(user_id)
            if user_key not in self._preloaded_contexts:
                return {"nodes": 0, "edges": 0, "density": 0.0}

            graph = self._preloaded_contexts[user_key]["graph"]
            return {
                "nodes": graph.number_of_nodes(),
                "edges": graph.number_of_edges(),
                "density": nx.density(graph) if graph.number_of_nodes() > 0 else 0.0,
                "degree_centrality": dict(
                    nx.degree_centrality(graph)
                ) if graph.number_of_nodes() > 0 else {},
            }
        return self._get_instance().get_graph_summary(user_id)

    def load_graph_edge(
        self,
        user_id: str,
        source: str,
        target: str,
        relation: str,
        weight: float,
        source_type: Optional[str] = None,
        target_type: Optional[str] = None,
    ) -> None:
        if self._instance is not None:
            self._instance.load_graph_edge(
                user_id, source, target, relation, weight, source_type, target_type
            )
            return

        user_key = str(user_id)
        if user_key not in self._preloaded_contexts:
            self._preloaded_contexts[user_key] = _empty_user_context()

        context = self._preloaded_contexts[user_key]
        context["graph"].add_edge(source, target, relation=relation, weight=weight)
        if source_type == "entity" and source not in context["entities"]:
            context["entities"].append(source)
        if target_type == "entity" and target not in context["entities"]:
            context["entities"].append(target)
        if source_type == "action" and source not in context["actions"]:
            context["actions"].append(source)
        if target_type == "action" and target not in context["actions"]:
            context["actions"].append(target)
        if source_type == "object" and source not in context["objects"]:
            context["objects"].append(source)
        if target_type == "object" and target not in context["objects"]:
            context["objects"].append(target)

    def _get_instance(self):
        """Get instance synchronously (for non-async calls)."""
        if self._instance is None:
            self._instance = EngramProcessor(self._preloaded_contexts)
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._get_instance(), name)


engram_processor = LazyEngramProcessor()
