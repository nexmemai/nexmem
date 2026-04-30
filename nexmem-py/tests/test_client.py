import json

import httpx
import pytest

from nexmem import MemoryClient
from nexmem.exceptions import NexMemAuthError


def json_response(data, status_code=200):
    return httpx.Response(
        status_code,
        content=json.dumps(data).encode(),
        headers={"content-type": "application/json"},
    )


@pytest.mark.asyncio
async def test_remember_posts_episode_write():
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "ApiKey mem_test"
        assert request.url.path == "/api/v1/memory/episode/write"
        body = json.loads(request.content)
        assert body["content"] == "User prefers Python."
        assert body["app_id"] == "app_1"
        assert body["metadata"] == {"source": "test"}
        return json_response({
            "episodic_id": "ep_1",
            "semantic_id": "sem_1",
            "engram_id": "eng_1",
            "nodes_created": 1,
            "edges_created": 0,
            "message": "ok",
        })

    transport = httpx.MockTransport(handler)
    async with MemoryClient("mem_test", "https://api.test", transport=transport) as client:
        episode = await client.remember(
            "User prefers Python.",
            app_id="app_1",
            metadata={"source": "test"},
        )

    assert episode.episodic_id == "ep_1"
    assert episode.nodes_created == 1
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_recall_returns_context_model():
    async def handler(request):
        assert request.url.path == "/api/v1/memory/context"
        body = json.loads(request.content)
        assert body["query"] == "language preference?"
        assert body["semantic_top_k"] == 3
        assert body["episodic_limit"] == 3
        return json_response({
            "assembled_context": "User prefers Python.",
            "engram_context": "Known entities: Python",
            "semantic_hits": [{"content_preview": "Python"}],
            "recent_episodes": [],
            "preferences": {},
            "graph_context": {},
            "metadata": {"total_tokens": 3},
        })

    transport = httpx.MockTransport(handler)
    async with MemoryClient("mem_test", "https://api.test", transport=transport) as client:
        context = await client.recall("language preference?", limit=3)

    assert context.content == "User prefers Python."
    assert context.memories.content == "User prefers Python."
    assert context.metadata["total_tokens"] == 3


@pytest.mark.asyncio
async def test_export_fetches_authenticated_user_then_export():
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/auth/me":
            return json_response({"id": "user_1"})
        if request.url.path == "/api/v1/memory/user/user_1/export":
            return json_response({"user_id": "user_1", "episodic": []})
        raise AssertionError(request.url.path)

    transport = httpx.MockTransport(handler)
    async with MemoryClient("mem_test", "https://api.test", transport=transport) as client:
        data = await client.export()

    assert data["user_id"] == "user_1"
    assert paths == ["/api/v1/auth/me", "/api/v1/memory/user/user_1/export"]


@pytest.mark.asyncio
async def test_forget_all_requires_confirmation():
    async with MemoryClient("mem_test", "https://api.test", transport=httpx.MockTransport(lambda request: json_response({}))) as client:
        with pytest.raises(ValueError):
            await client.forget_all()


@pytest.mark.asyncio
async def test_api_auth_error():
    async def handler(request):
        return json_response({"detail": "Nope"}, status_code=403)

    async with MemoryClient("mem_test", "https://api.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NexMemAuthError):
            await client.export()
