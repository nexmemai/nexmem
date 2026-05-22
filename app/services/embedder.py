"""
Embedding service using sentence-transformers for local, async-safe embeddings.

Phase 2 changes:
* Switched the per-event-loop semaphore from a single shared one
  (``Semaphore(1)``) to the named-pool primitives in
  ``app/core/concurrency.py``. The cap is now 4 per loop instead of
  1, which lets the embedder serve concurrent requests without
  spawning unbounded threads.
* ``get_nlp_semaphore`` is kept as a backwards-compatible alias so
  any third-party code that imports it does not break.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional

from app.core.concurrency import get_semaphore, run_bounded

logger = logging.getLogger(__name__)


def get_nlp_semaphore():
    """Backwards-compatible alias for code that imported it from here."""
    return get_semaphore("nlp")


class Embedder:
    """Service for generating text embeddings using sentence-transformers."""

    def __init__(self):
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(1)
        t0 = time.perf_counter()
        self._embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        self.vector_dim = 384
        load_ms = (time.perf_counter() - t0) * 1000
        logger.info("embedder.model_loaded", extra={"model": "all-MiniLM-L6-v2", "load_ms": round(load_ms, 1)})

    async def embed(self, text: str) -> List[float]:
        return await run_bounded(
            "embedder",
            lambda: self._embed_model.encode(text, normalize_embeddings=True).tolist(),
        )

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return await run_bounded(
            "embedder",
            lambda: self._embed_model.encode(texts, normalize_embeddings=True).tolist(),
        )

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        import numpy as np

        v1 = np.array(vec1)
        v2 = np.array(vec2)
        return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

    def random_vector(self) -> List[float]:
        import numpy as np

        vec = np.random.randn(self.vector_dim)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()


class LazyEmbedder:
    """Create the sentence-transformers model only on first use."""

    vector_dim = 384

    def __init__(self):
        self._instance: Optional[Embedder] = None
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _get(self) -> Embedder:
        if self._instance is not None:
            return self._instance
        lock = self._get_lock()
        async with lock:
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
        import numpy as np

        vec = np.random.randn(self.vector_dim)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()


# Global embedder instance
embedder = LazyEmbedder()
