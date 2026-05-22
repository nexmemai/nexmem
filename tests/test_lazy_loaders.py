"""Cold-start guards for the lazy-loaded ML services (P2-C7).

These tests:
  - Prove the LazyEmbedder and LazyEngramProcessor only construct
    their underlying heavy object once, even under concurrent calls.
  - Prove warmup() is idempotent.
  - Prove the loaders log a single 'warmup_complete' event on the
    first construction.

We patch the underlying heavy class with a counter-bumping fake so
the test does not actually load spaCy / sentence-transformers (which
would require huggingface downloads).
"""

from __future__ import annotations

import asyncio
import logging

import pytest


# ── LazyEmbedder ───────────────────────────────────────────────────────────


class _FakeEmbedder:
    instances_constructed = 0

    def __init__(self):
        type(self).instances_constructed += 1
        self.vector_dim = 384

    async def embed(self, text: str):
        return [0.0] * self.vector_dim

    async def embed_batch(self, texts):
        return [[0.0] * self.vector_dim for _ in texts]


@pytest.mark.asyncio
async def test_lazy_embedder_singleton_under_concurrent_first_calls(
    monkeypatch, caplog
) -> None:
    from app.services import embedder as emb_mod

    _FakeEmbedder.instances_constructed = 0
    monkeypatch.setattr(emb_mod, "Embedder", _FakeEmbedder)

    lazy = emb_mod.LazyEmbedder()

    caplog.set_level(logging.INFO, logger="app.services.embedder")
    await asyncio.gather(*(lazy.embed("x") for _ in range(20)))

    assert _FakeEmbedder.instances_constructed == 1, (
        f"expected exactly one Embedder construction across concurrent first "
        f"calls; got {_FakeEmbedder.instances_constructed}. "
        f"Race in LazyEmbedder._get."
    )
    # Exactly one warmup_complete event in the captured log.
    warmup_logs = [r for r in caplog.records if "warmup_complete" in r.getMessage()]
    assert len(warmup_logs) == 1, (
        f"expected one 'embedder.warmup_complete' log line, got {len(warmup_logs)}"
    )


@pytest.mark.asyncio
async def test_lazy_embedder_warmup_is_idempotent(monkeypatch) -> None:
    from app.services import embedder as emb_mod

    _FakeEmbedder.instances_constructed = 0
    monkeypatch.setattr(emb_mod, "Embedder", _FakeEmbedder)

    lazy = emb_mod.LazyEmbedder()
    await lazy.warmup()
    await lazy.warmup()
    await lazy.warmup()

    assert _FakeEmbedder.instances_constructed == 1, (
        "warmup() must be idempotent; second + third calls reused the cached instance."
    )


# ── LazyEngramProcessor ────────────────────────────────────────────────────


class _FakeEngramProcessor:
    instances_constructed = 0

    def __init__(self, preloaded_contexts=None):
        type(self).instances_constructed += 1
        self._user_contexts = preloaded_contexts or {}

    async def process_async(self, text: str, user_id: str):
        return {"engram_id": "fake12345678"}

    def get_compressed_context(self, *_a, **_kw):
        return ""

    def get_graph_summary(self, *_a, **_kw):
        return {"nodes": 0, "edges": 0, "density": 0.0}

    def load_graph_edge(self, *_a, **_kw):
        return None


@pytest.mark.asyncio
async def test_lazy_engram_processor_singleton_under_concurrent_first_calls(
    monkeypatch, caplog
) -> None:
    from app.services import engram_processor as ep_mod

    _FakeEngramProcessor.instances_constructed = 0
    monkeypatch.setattr(ep_mod, "EngramProcessor", _FakeEngramProcessor)

    lazy = ep_mod.LazyEngramProcessor()

    caplog.set_level(logging.INFO, logger="app.services.engram_processor")
    await asyncio.gather(
        *(lazy.process_async("hello", "00000000-0000-0000-0000-000000000001") for _ in range(20))
    )

    assert _FakeEngramProcessor.instances_constructed == 1, (
        f"expected exactly one EngramProcessor construction; got "
        f"{_FakeEngramProcessor.instances_constructed}. Race in "
        f"LazyEngramProcessor._get."
    )
    warmup_logs = [
        r for r in caplog.records if "engram_processor.warmup_complete" in r.getMessage()
    ]
    assert len(warmup_logs) == 1


@pytest.mark.asyncio
async def test_lazy_engram_processor_warmup_is_idempotent(monkeypatch) -> None:
    from app.services import engram_processor as ep_mod

    _FakeEngramProcessor.instances_constructed = 0
    monkeypatch.setattr(ep_mod, "EngramProcessor", _FakeEngramProcessor)

    lazy = ep_mod.LazyEngramProcessor()
    await lazy.warmup()
    await lazy.warmup()
    assert _FakeEngramProcessor.instances_constructed == 1


# ── Settings flag ──────────────────────────────────────────────────────────


def test_warm_models_at_startup_setting_exists() -> None:
    from app.config import Settings

    s = Settings(
        environment="development",
        demo_mode=True,
        secret_key="x" * 64,
        database_url="postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/x",
        openai_api_key="sk-test",
    )
    # Default should be False so dev / CI do not download models.
    assert s.warm_models_at_startup is False
