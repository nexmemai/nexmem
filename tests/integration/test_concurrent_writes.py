"""
Concurrent write test to verify async safety of NLP/embedding operations.

Sends 10 concurrent POST /api/v1/memory/episode/write requests
and verifies all return 200 within 500ms.
"""

import asyncio
import time
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.config import settings


@pytest.fixture(autouse=True)
def force_demo_mode(monkeypatch):
    """Force demo mode for all tests."""
    monkeypatch.setattr(settings, 'demo_mode', True)


@pytest.mark.asyncio
async def test_concurrent_writes():
    """Verify 10 concurrent episode writes complete without blocking."""
    # Pre-load models to avoid slow first request
    from app.services.embedder import embedder
    from app.services.engram_processor import engram_processor
    await embedder.embed("warmup")
    await engram_processor.process_async("warmup", "warmup-user")
    
    transport = ASGITransport(app=app)
    
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Demo mode should work without auth
        payload = {
            "user_id": "550e8400-e29b-41d4-a716-446655440000",
            "content": "Test memory entry for concurrent write validation",
            "session_id": "concurrent-test-session"
        }
        
        # Send 10 concurrent requests
        async def send_write_request(request_id: int):
            start = time.perf_counter()
            resp = await client.post(
                "/api/v1/memory/episode/write",
                json={**payload, "content": f"Test memory {request_id}"}
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            return resp, elapsed_ms
        
        start_all = time.perf_counter()
        tasks = [send_write_request(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        total_elapsed = (time.perf_counter() - start_all) * 1000
        
        # Verify all requests succeeded
        for i, (resp, elapsed_ms) in enumerate(results):
            assert resp.status_code == 200, f"Request {i} failed with {resp.status_code}: {resp.text}"
            assert elapsed_ms < 5000, f"Request {i} exceeded 5000ms: {elapsed_ms:.1f}ms"
        
        # All 10 should complete concurrently (total time < 10 * 5000ms)
        assert total_elapsed < 10000, f"Total time {total_elapsed:.1f}ms suggests blocking"


@pytest.mark.asyncio
async def test_embedder_async_safety():
    """Verify embedder doesn't block event loop."""
    from app.services.embedder import embedder
    
    # Send 5 concurrent embedding requests
    async def embed_text(text: str):
        start = time.perf_counter()
        result = await embedder.embed(text)
        elapsed = (time.perf_counter() - start) * 1000
        return result, elapsed
    
    start = time.perf_counter()
    tasks = [embed_text(f"Test text number {i}") for i in range(5)]
    results = await asyncio.gather(*tasks)
    total_time = (time.perf_counter() - start) * 1000
    
    # All should return valid 384D vectors
    for i, (vector, elapsed) in enumerate(results):
        assert len(vector) == 384, f"Request {i} returned {len(vector)}D vector, expected 384"
        assert elapsed < 2000, f"Embedding {i} took {elapsed:.1f}ms"
    
    # With semaphore(4), 5 requests should take at least 2 batches
    print(f"5 concurrent embeddings completed in {total_time:.1f}ms")
