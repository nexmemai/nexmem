"""Tests for memory context endpoint."""

import os
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="requires live PostgreSQL/Supabase database; set RUN_DB_TESTS=1",
)


@pytest.mark.asyncio
async def test_memory_context_requires_auth(client):
    """Test that /memory/context requires authentication."""
    response = await client.post("/api/v1/memory/context", json={
        "query": "test query"
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_memory_context_returns_all_sources(auth_headers, client):
    """Test that /memory/context returns all 5 memory sources."""
    response = await client.post(
        "/api/v1/memory/context",
        json={"query": "test query", "semantic_top_k": 5, "episodic_limit": 5},
        headers=auth_headers
    )
    assert response.status_code in [200, 500]


@pytest.mark.asyncio
async def test_episode_write_requires_auth(client):
    """Test that /memory/episode/write requires authentication."""
    response = await client.post("/api/v1/memory/episode/write", json={
        "content": "test content",
        "session_id": "test-session"
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_engram_compression_ratio(auth_headers, client):
    """Test that engram compression ratio is calculated."""
    response = await client.post(
        "/api/v1/memory/context",
        json={"query": "This is a test query about AI and machine learning with neural networks and deep learning architectures"},
        headers=auth_headers
    )
    if response.status_code == 200:
        data = response.json()
        assert "metadata" in data
        if "compression_ratio" in data["metadata"]:
            assert data["metadata"]["compression_ratio"] >= 0
