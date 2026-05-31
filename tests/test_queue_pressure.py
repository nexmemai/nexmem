"""Tests for Celery queue backpressure (P6-D8, Block 7).

Three spec'd behaviours:

* Queue depth above limit → write request 503s.
* Queue depth at or under limit → write request succeeds.
* Redis unavailable → fail-open: write request still succeeds
  (R-301 posture).

The dependency is wired on ``POST /memory/episode/write`` in
``app/routers/memory.py``. We monkeypatch ``get_queue_depth``
directly — exercising the real Redis client requires a broker
that is not available in the sandbox.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

import app.core.queue_pressure as qp
from app.config import settings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def write_payload():
    return {"content": "queue pressure test", "session_id": "qp-test"}


@pytest.fixture(autouse=True)
def fast_ml(monkeypatch):
    """Same NLP fast-path as ``test_app_suspension.py``. Tests in this
    file exercise the write route to drive the dependency, not the NLP
    pipeline."""
    from app.services import embedder as embedder_module
    from app.services import engram_processor as engram_module

    async def _fake_embed(_text: str):
        return embedder_module.embedder.random_vector()

    async def _fake_engram(text: str, user_id: str):
        return {
            "engram_id": "fake-engram-id",
            "distilled_text": text[:200],
            "compression_ratio": 1.0,
            "actions": [],
            "objects": [],
            "entities": [],
            "negated_actions": [],
            "salience_scores": {},
            "connections": [],
            "graph_edges": [],
            "dense_embedding": None,
            "original_length": len(text),
            "compressed_length": min(len(text), 200),
        }

    monkeypatch.setattr(embedder_module.embedder, "embed", _fake_embed)
    monkeypatch.setattr(
        engram_module.engram_processor, "process_async", _fake_engram
    )


# ── 1. Over the limit → 503 ──────────────────────────────────────────────────
async def test_write_blocked_when_queue_over_limit(
    client: AsyncClient, auth_headers, write_payload, monkeypatch
):
    """Depth limit is 1000 by default; we report 5000 to trigger
    the 503 path."""
    async def _depth(_queue_name="celery"):
        return 5000

    monkeypatch.setattr(qp, "get_queue_depth", _depth)

    r = await client.post(
        "/api/v1/memory/episode/write",
        json=write_payload,
        headers=auth_headers,
    )
    assert r.status_code == 503, r.text
    body = r.json()
    detail = body.get("detail", body)
    assert detail["error"] == "service_overloaded"
    assert detail["queue_depth"] == 5000
    assert detail["limit"] == settings.celery_queue_depth_limit


# ── 2. Under the limit → request flows ───────────────────────────────────────
async def test_write_allowed_when_queue_under_limit(
    client: AsyncClient, auth_headers, write_payload, monkeypatch
):
    """Depth equal to the limit must NOT 503 — the threshold is
    strictly greater-than."""
    async def _depth(_queue_name="celery"):
        return settings.celery_queue_depth_limit  # exactly at the limit

    monkeypatch.setattr(qp, "get_queue_depth", _depth)

    r = await client.post(
        "/api/v1/memory/episode/write",
        json=write_payload,
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text


# ── 3. Redis down → fail-open ────────────────────────────────────────────────
async def test_write_allowed_when_redis_unavailable(
    client: AsyncClient, auth_headers, write_payload, monkeypatch
):
    """If the Redis lookup raises, ``get_queue_depth`` returns 0 and
    the dependency is a no-op. Writes continue to flow.

    This matches R-301 in BACKEND_RISKS.md — Redis fail-open is
    accepted-for-private-beta. A fail-closed posture here would
    turn a Redis blip into a write outage.
    """
    # Drive the real ``get_queue_depth`` through a broken aioredis path
    # by patching ``redis.asyncio.from_url`` to raise. We are NOT
    # patching get_queue_depth itself so the swallow-and-return-0
    # behaviour is exercised end-to-end.
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "redis_url", "redis://does-not-exist:6379/0")

    import redis.asyncio as aioredis

    def _boom(*a, **k):
        raise RuntimeError("simulated redis outage")

    monkeypatch.setattr(aioredis, "from_url", _boom)

    # ``get_queue_depth`` should return 0 despite the broken aioredis.
    depth = await qp.get_queue_depth()
    assert depth == 0

    # The endpoint sits behind ``check_queue_pressure``, which now
    # sees depth=0 and is a no-op. Other dependencies (auth, quota)
    # may still fire — we restore demo_mode so the route reaches
    # its happy-path body.
    monkeypatch.setattr(settings, "demo_mode", True)
    r = await client.post(
        "/api/v1/memory/episode/write",
        json=write_payload,
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text


# ── 4. Disabled when limit <= 0 ──────────────────────────────────────────────
async def test_backpressure_disabled_when_limit_zero(
    client: AsyncClient, auth_headers, write_payload, monkeypatch
):
    """``celery_queue_depth_limit = 0`` is the documented operator
    opt-out. The dependency must short-circuit BEFORE calling
    ``get_queue_depth`` (so a Redis call is not even made)."""
    monkeypatch.setattr(settings, "celery_queue_depth_limit", 0)

    called = {"depth": False}

    async def _spy(_queue_name="celery"):
        called["depth"] = True
        return 999_999

    monkeypatch.setattr(qp, "get_queue_depth", _spy)

    r = await client.post(
        "/api/v1/memory/episode/write",
        json=write_payload,
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert called["depth"] is False, "depth lookup should be skipped"
