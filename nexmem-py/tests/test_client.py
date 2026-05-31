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
        assert request.headers["authorization"] == "ApiKey nxm_test"
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
    async with MemoryClient("nxm_test", "https://api.test", transport=transport) as client:
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
    async with MemoryClient("nxm_test", "https://api.test", transport=transport) as client:
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
    async with MemoryClient("nxm_test", "https://api.test", transport=transport) as client:
        data = await client.export()

    assert data["user_id"] == "user_1"
    assert paths == ["/api/v1/auth/me", "/api/v1/memory/user/user_1/export"]


@pytest.mark.asyncio
async def test_forget_all_requires_confirmation():
    async with MemoryClient("nxm_test", "https://api.test", transport=httpx.MockTransport(lambda request: json_response({}))) as client:
        with pytest.raises(ValueError):
            await client.forget_all()


@pytest.mark.asyncio
async def test_api_auth_error():
    async def handler(request):
        return json_response({"detail": "Nope"}, status_code=403)

    async with MemoryClient("nxm_test", "https://api.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NexMemAuthError):
            await client.export()



# ─────────────────────────── P12-J1 (Block 8) additions ────────────────────
# Three tests the spec singled out that were not present in the original
# file: set_profile, get_profile, and the SyncMemoryClient wrapper. All
# three follow the same MockTransport posture as the tests above so we
# do not introduce a new mocking framework (respx) to satisfy them.


@pytest.mark.asyncio
async def test_set_profile_calls_correct_endpoint():
    """``set_profile`` does:

      1. GET /auth/me to discover user_id (cached after first call).
      2. GET /agents/{user_id}/procedural/settings to read existing profile.
      3. POST /agents/{user_id}/procedural/settings with the merged dict.

    The third call must include both the existing keys (preserved) and the
    new key (added).
    """
    captured: dict[str, object] = {}
    paths: list[str] = []

    async def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/auth/me":
            return json_response({"id": "user_1"})
        if request.url.path == "/api/v1/agents/user_1/procedural/settings":
            if request.method == "GET":
                return json_response({"settings": {"existing": "kept"}, "workflows": []})
            # POST: capture the merged body so we can assert below.
            captured["body"] = json.loads(request.content)
            return json_response({"upserted": True})
        raise AssertionError(request.url.path)

    transport = httpx.MockTransport(handler)
    async with MemoryClient("nxm_test", "https://api.test", transport=transport) as client:
        await client.set_profile("tone", "concise")

    # The merged settings dict was POSTed back.
    assert captured["body"]["settings"] == {"existing": "kept", "tone": "concise"}
    # Three round-trips: auth/me, GET, POST.
    assert paths == [
        "/api/v1/auth/me",
        "/api/v1/agents/user_1/procedural/settings",
        "/api/v1/agents/user_1/procedural/settings",
    ]


@pytest.mark.asyncio
async def test_get_profile_calls_correct_endpoint():
    """``get_profile`` returns just the ``settings`` sub-dict, not the
    whole payload. Also exercises the user-id caching path."""

    async def handler(request):
        if request.url.path == "/api/v1/auth/me":
            return json_response({"id": "user_2"})
        if request.url.path == "/api/v1/agents/user_2/procedural/settings":
            assert request.method == "GET"
            return json_response({
                "settings": {"theme": "dark", "language": "en"},
                "workflows": [{"name": "noop"}],
            })
        raise AssertionError(request.url.path)

    transport = httpx.MockTransport(handler)
    async with MemoryClient("nxm_test", "https://api.test", transport=transport) as client:
        profile = await client.get_profile()

    # Only ``settings`` is returned — workflows are stripped.
    assert profile == {"theme": "dark", "language": "en"}


def test_sync_client_wraps_async_methods(monkeypatch):
    """``SyncMemoryClient.remember`` blocks on the async coroutine via
    ``asyncio.run`` and returns the same Episode shape.

    We don't drive the async client through MockTransport here (the
    sync wrapper instantiates its own ``MemoryClient`` inside a fresh
    event loop, which would need a transport injected per-call — a
    fragile shape). Instead we monkeypatch ``MemoryClient.remember``
    to a no-network coroutine and assert the wrapper drives it.
    """
    import asyncio

    from nexmem.client import MemoryClient as AsyncClient
    from nexmem.models import Episode
    from nexmem.sync_client import SyncMemoryClient

    sentinel = Episode(episodic_id="ep_sync", message="ok", raw={})

    async def fake_remember(self, text, app_id=None, metadata=None):
        # Confirm the wrapper passed args through verbatim.
        assert text == "remember from sync"
        assert app_id == "app_sync"
        assert metadata == {"who": "sync"}
        return sentinel

    monkeypatch.setattr(AsyncClient, "remember", fake_remember)

    # The fake also needs an aclose to satisfy __aexit__ on the inner
    # ``async with``. The real one closes httpx's pool; ours is a no-op.
    async def fake_aclose(self):
        return None

    monkeypatch.setattr(AsyncClient, "aclose", fake_aclose)

    client = SyncMemoryClient("nxm_test", "https://api.test")
    try:
        episode = client.remember(
            "remember from sync", app_id="app_sync", metadata={"who": "sync"}
        )
    finally:
        client.close()

    assert episode is sentinel
    assert episode.episodic_id == "ep_sync"
