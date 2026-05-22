"""Bounded concurrency primitives for CPU-heavy work in async routes.

Phase 2 (R-106): every heavy synchronous call (sentence-transformers
embed, spaCy NLP pipeline, cross-encoder rerank) must run in a thread
pool executor AND be capped by a per-event-loop semaphore so we do
not spawn unbounded threads under load.

Semaphores are created lazily per running event loop because asyncio
primitives are bound to a loop. The wrapping helper ``run_bounded``
acquires the semaphore, dispatches to ``run_in_executor``, and
returns the result.

Caps:
* ``embedder``: 4 — the 384-dim model encodes a single short text in
  a few ms, but vector creation is GIL-bound and PyTorch is already
  pinned to 1 thread; 4 is comfortable for a 2-vCPU instance.
* ``nlp``: 4 — spaCy en_core_web_sm is single-threaded; 4 keeps the
  thread pool from runaway under burst traffic.
* ``reranker``: 2 — the cross-encoder is heavier (12-layer transformer)
  so the cap is lower to avoid head-of-line blocking on small servers.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

# Per-pool cap. Keep small — these are CPU-heavy paths.
_CAPS = {
    "embedder": 4,
    "nlp": 4,
    "reranker": 2,
}


def _slot_attr(name: str) -> str:
    return f"_nexmem_sem_{name}"


def get_semaphore(pool: str) -> asyncio.Semaphore:
    """Return the semaphore for ``pool`` bound to the current loop.

    Stores the semaphore on the running event loop instance under a
    distinct attribute per pool so concurrent embedder + nlp work do
    not contend on the same lock.
    """
    if pool not in _CAPS:
        raise ValueError(f"unknown concurrency pool: {pool!r}")
    loop = asyncio.get_running_loop()
    attr = _slot_attr(pool)
    sem = getattr(loop, attr, None)
    if sem is None:
        sem = asyncio.Semaphore(_CAPS[pool])
        setattr(loop, attr, sem)
    return sem


async def run_bounded(
    pool: str, fn: Callable[..., T], /, *args: Any, **kwargs: Any
) -> T:
    """Run ``fn(*args, **kwargs)`` in the default executor under the pool cap.

    Use this for any synchronous CPU-heavy work called from an async
    route handler. Direct ``run_in_executor`` calls without a cap can
    spawn unbounded threads under burst traffic.
    """
    sem = get_semaphore(pool)
    loop = asyncio.get_running_loop()
    async with sem:
        return await loop.run_in_executor(
            None, lambda: fn(*args, **kwargs)
        )
