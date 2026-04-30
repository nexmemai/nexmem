"""Async NexMem SDK client."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from nexmem.exceptions import NexMemAPIError, NexMemAuthError
from nexmem.models import Context, Episode


class MemoryClient:
    """Async client for the NexMem API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://nexmem-api.onrender.com",
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._user_id: str | None = None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"ApiKey {api_key}",
                "Accept": "application/json",
                "User-Agent": "nexmem-py/0.1.0",
            },
        )

    async def __aenter__(self) -> "MemoryClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def remember(
        self,
        text: str,
        app_id: str | None = None,
        metadata: dict | None = None,
    ) -> Episode:
        """Store a memory episode."""
        payload: dict[str, Any] = {
            "content": text,
            "session_id": str(uuid4()),
            "metadata": metadata or {},
            "tags": [],
            "app_id": app_id,
        }
        data = await self._request("POST", "/api/v1/memory/episode/write", json=payload)
        return Episode.from_dict(data)

    async def recall(
        self,
        query: str,
        limit: int = 5,
        app_id: str | None = None,
    ) -> Context:
        """Recall relevant context for a query."""
        payload = {
            "query": query,
            "semantic_top_k": limit,
            "episodic_limit": limit,
            "app_id": app_id,
        }
        data = await self._request("POST", "/api/v1/memory/context", json=payload)
        return Context.from_dict(data)

    async def set_profile(self, key: str, value: Any) -> None:
        """Set one profile key in procedural memory settings."""
        user_id = await self._get_user_id()
        profile = await self.get_profile()
        profile[key] = value
        await self._request(
            "POST",
            f"/api/v1/agents/{user_id}/procedural/settings",
            json={"settings": profile, "workflows": []},
        )

    async def get_profile(self) -> dict:
        """Return procedural profile settings."""
        user_id = await self._get_user_id()
        data = await self._request("GET", f"/api/v1/agents/{user_id}/procedural/settings")
        return data.get("settings", {})

    async def link(self, entity1: str, relation: str, entity2: str) -> None:
        """Create a graph link between two entity labels."""
        user_id = await self._get_user_id()
        first = await self._request(
            "POST",
            f"/api/v1/agents/{user_id}/graph/nodes",
            json={"label": entity1, "type": "entity", "properties": {}},
        )
        second = await self._request(
            "POST",
            f"/api/v1/agents/{user_id}/graph/nodes",
            json={"label": entity2, "type": "entity", "properties": {}},
        )
        await self._request(
            "POST",
            f"/api/v1/agents/{user_id}/graph/edges",
            json={
                "from_node_id": first["id"],
                "to_node_id": second["id"],
                "relation": relation,
                "weight": 1.0,
                "metadata": {},
            },
        )

    async def forget_all(self, confirm: bool = False) -> None:
        """Delete all memories and invalidate the authenticated user."""
        if not confirm:
            raise ValueError("Pass confirm=True to permanently delete all memories")
        user_id = await self._get_user_id()
        await self._request(
            "DELETE",
            f"/api/v1/memory/user/{user_id}/all",
            headers={"X-Confirm-Delete": "true"},
        )
        self._user_id = None

    async def export(self) -> dict:
        """Export all memories for the authenticated user."""
        user_id = await self._get_user_id()
        return await self._request("GET", f"/api/v1/memory/user/{user_id}/export")

    async def _get_user_id(self) -> str:
        if self._user_id is None:
            data = await self._request("GET", "/api/v1/auth/me")
            self._user_id = data["id"]
        return self._user_id

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            message = self._error_message(response)
            error_cls = NexMemAuthError if response.status_code in {401, 403} else NexMemAPIError
            raise error_cls(response.status_code, message, response)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text
        detail = body.get("detail", body)
        return detail if isinstance(detail, str) else str(detail)
