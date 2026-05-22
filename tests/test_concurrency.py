"""Phase 2 unit tests for the bounded-concurrency primitives.

The named pools in app/core/concurrency.py back every CPU-heavy
async path (embedder, nlp, reranker). These tests pin:

* The semaphore for a pool is constructed lazily and cached on the
  running event loop. Repeated calls return the same instance for
  the same loop / pool.
* ``run_bounded`` actually offloads the call to a thread (so the
  event loop is not blocked) and respects the configured cap.
* Unknown pool names raise. Typos cannot silently degrade to an
  unbounded thread pool.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.core import concurrency


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_get_semaphore_returns_singleton_per_pool():
    sem1 = concurrency.get_semaphore("nlp")
    sem2 = concurrency.get_semaphore("nlp")
    assert sem1 is sem2


async def test_get_semaphore_distinct_pools_have_distinct_locks():
    nlp_sem = concurrency.get_semaphore("nlp")
    embedder_sem = concurrency.get_semaphore("embedder")
    reranker_sem = concurrency.get_semaphore("reranker")
    assert nlp_sem is not embedder_sem
    assert embedder_sem is not reranker_sem
    assert nlp_sem is not reranker_sem


async def test_unknown_pool_raises():
    with pytest.raises(ValueError):
        concurrency.get_semaphore("not-a-real-pool")


async def test_run_bounded_offloads_to_executor_thread():
    main_thread = threading.get_ident()

    def get_thread_id() -> int:
        return threading.get_ident()

    worker = await concurrency.run_bounded("nlp", get_thread_id)
    assert worker != main_thread


async def test_run_bounded_caps_concurrency_at_pool_limit():
    """The reranker pool is capped at 2; pin that ceiling holds."""
    cap = concurrency._CAPS["reranker"]
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def slow_call() -> None:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1

    await asyncio.gather(*(
        concurrency.run_bounded("reranker", slow_call) for _ in range(cap * 3)
    ))
    assert peak <= cap, f"reranker pool exceeded its cap of {cap}: peak={peak}"


async def test_run_bounded_propagates_args_and_kwargs():
    def add(a: int, b: int = 0) -> int:
        return a + b

    out = await concurrency.run_bounded("nlp", add, 2, b=3)
    assert out == 5


async def test_run_bounded_propagates_exception():
    def boom() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await concurrency.run_bounded("nlp", boom)
