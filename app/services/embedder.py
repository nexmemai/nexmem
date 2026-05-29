"""
Embedding service using sentence-transformers for local, async-safe embeddings.
"""

import asyncio
import logging
import time
from typing import List, Optional


logger = logging.getLogger(__name__)

# Semaphore to cap parallel NLP/embedding jobs (created per-event-loop)
def get_nlp_semaphore():
    """Get or create semaphore for current event loop."""
    if not hasattr(asyncio.get_running_loop(), '_nlp_semaphore'):
        asyncio.get_running_loop()._nlp_semaphore = asyncio.Semaphore(1)
    return asyncio.get_running_loop()._nlp_semaphore


class Embedder:
    """Service for generating text embeddings using sentence-transformers."""

    def __init__(self):
        import torch
        from sentence_transformers import SentenceTransformer
        # Prevent PyTorch from spawning too many threads inside the executor (deadlocks)
        torch.set_num_threads(1)
        self._embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        self.vector_dim = 384  # Fixed to 384D for all-MiniLM-L6-v2

    async def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text (async-safe).
        Runs in a thread-pool executor to avoid blocking the event loop.
        """
        loop = asyncio.get_running_loop()
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
        loop = asyncio.get_running_loop()
        sem = get_nlp_semaphore()
        async with sem:
            return await loop.run_in_executor(
                None,
                lambda: self._embed_model.encode(texts, normalize_embeddings=True).tolist()
            )

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        import numpy as np
        v1 = np.array(vec1)
        v2 = np.array(vec2)
        return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

    def random_vector(self) -> List[float]:
        """Generate a random normalized vector for demo/fallback purposes."""
        import numpy as np
        vec = np.random.randn(self.vector_dim)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()


class LazyEmbedder:
    """Create the sentence-transformers model only when embeddings are used.

    Cold-start behaviour (R-M3, P2-C7):
      - First call to embed() loads the model on the request thread.
        Loading is single-flighted by an asyncio.Lock so concurrent
        first-callers wait for one shared init, never duplicate work.
      - First-load duration is logged (`embedder.warmup_complete` event)
        so operators can correlate p99 latency spikes with cold starts.
      - Operators can pre-warm at startup by calling warmup(); the
        lifespan in app.main does this when settings.warm_models_at_startup
        is true.
    """

    vector_dim = 384

    def __init__(self):
        self._instance: Optional[Embedder] = None
        self._lock: Optional[asyncio.Lock] = None  # Created lazily per event-loop
        self._first_load_logged: bool = False

    def _get_lock(self) -> asyncio.Lock:
        """Return a lock bound to the current running event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _get(self) -> Embedder:
        if self._instance is not None:
            return self._instance
        lock = self._get_lock()
        async with lock:
            if self._instance is None:
                t0 = time.perf_counter()
                # Load on a worker thread so the event loop is not pinned
                # for the duration of model loading.
                loop = asyncio.get_running_loop()
                self._instance = await loop.run_in_executor(None, Embedder)
                if not self._first_load_logged:
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    logger.info(
                        "embedder.warmup_complete model=%s elapsed_ms=%.2f",
                        "all-MiniLM-L6-v2",
                        elapsed_ms,
                    )
                    self._first_load_logged = True
        return self._instance

    async def warmup(self) -> None:
        """Eagerly load the model. Safe to call multiple times.

        Used by app lifespan when WARM_MODELS_AT_STARTUP=1 to move
        the cost off the first request path.
        """
        await self._get()

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
