"""
Embedding service using sentence-transformers for local, async-safe embeddings.
"""

import asyncio
import logging
from typing import List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Semaphore to cap parallel NLP/embedding jobs (created per-event-loop)
def get_nlp_semaphore():
    """Get or create semaphore for current event loop."""
    if not hasattr(asyncio.get_running_loop(), '_nlp_semaphore'):
        asyncio.get_running_loop()._nlp_semaphore = asyncio.Semaphore(4)
    return asyncio.get_running_loop()._nlp_semaphore


class Embedder:
    """Service for generating text embeddings using sentence-transformers."""

    def __init__(self):
        self._embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        self.vector_dim = 384  # Fixed to 384D for all-MiniLM-L6-v2

    async def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text (async-safe).
        """
        loop = asyncio.get_event_loop()
        sem = get_nlp_semaphore()
        async with sem:
            return await loop.run_in_executor(
                None,
                lambda: self._embed_model.encode(text, normalize_embeddings=True).tolist()
            )

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts (async-safe).
        """
        loop = asyncio.get_event_loop()
        sem = get_nlp_semaphore()
        async with sem:
            return await loop.run_in_executor(
                None,
                lambda: self._embed_model.encode(texts, normalize_embeddings=True).tolist()
            )

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        v1 = np.array(vec1)
        v2 = np.array(vec2)
        return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

    def random_vector(self) -> List[float]:
        """Generate a random normalized vector for demo/fallback purposes."""
        vec = np.random.randn(self.vector_dim)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()


class LazyEmbedder:
    """Create the sentence-transformers model only when embeddings are used."""

    vector_dim = 384

    def __init__(self):
        self._instance: Optional[Embedder] = None
        self._lock = asyncio.Lock()

    async def _get(self) -> Embedder:
        if self._instance is None:
            async with self._lock:
                if self._instance is None:
                    self._instance = Embedder()
        return self._instance

    async def embed(self, text: str) -> List[float]:
        instance = await self._get()
        return await instance.embed(text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        instance = await self._get()
        return await instance.embed_batch(texts)

    def random_vector(self) -> List[float]:
        vec = np.random.randn(self.vector_dim)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()


# Global embedder instance
embedder = LazyEmbedder()
EmbeddingService = Embedder
