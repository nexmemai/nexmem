"""Synchronous wrapper for the async NexMem client."""

from typing import Any

import httpx

from nexmem.client import MemoryClient
from nexmem.models import Context, Episode


class SyncMemoryClient:
    """Blocking client for non-async Python applications."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://nexmem-api.onrender.com",
        *,
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"ApiKey {api_key}",
                "Accept": "application/json",
                "User-Agent": "nexmem-py/0.1.0",
            },
        )
        self._user_id: str | None = None

    def __enter__(self) -> "SyncMemoryClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def remember(
        self,
        text: str,
        app_id: str | None = None,
        metadata: dict | None = None,
    ) -> Episode:
        return self._run_async("remember", text, app_id=app_id, metadata=metadata)

    def recall(
        self,
        query: str,
        limit: int = 5,
        app_id: str | None = None,
    ) -> Context:
        return self._run_async("recall", query, limit=limit, app_id=app_id)

    def set_profile(self, key: str, value: Any) -> None:
        user_id = self._get_user_id()
        profile = self.get_profile()
        profile[key] = value
        self._request(
            "POST",
            f"/api/v1/agents/{user_id}/procedural/settings",
            json={"settings": profile, "workflows": []},
        )

    def get_profile(self) -> dict:
        user_id = self._get_user_id()
        data = self._request("GET", f"/api/v1/agents/{user_id}/procedural/settings")
        return data.get("settings", {})

    def link(self, entity1: str, relation: str, entity2: str) -> None:
        user_id = self._get_user_id()
        first = self._request(
            "POST",
            f"/api/v1/agents/{user_id}/graph/nodes",
            json={"label": entity1, "type": "entity", "properties": {}},
        )
        second = self._request(
            "POST",
            f"/api/v1/agents/{user_id}/graph/nodes",
            json={"label": entity2, "type": "entity", "properties": {}},
        )
        self._request(
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

    def forget_all(self, confirm: bool = False) -> None:
        if not confirm:
            raise ValueError("Pass confirm=True to permanently delete all memories")
        user_id = self._get_user_id()
        self._request(
            "DELETE",
            f"/api/v1/memory/user/{user_id}/all",
            headers={"X-Confirm-Delete": "true"},
        )
        self._user_id = None

    def export(self) -> dict:
        user_id = self._get_user_id()
        return self._request("GET", f"/api/v1/memory/user/{user_id}/export")

    def _get_user_id(self) -> str:
        if self._user_id is None:
            data = self._request("GET", "/api/v1/auth/me")
            self._user_id = data["id"]
        return self._user_id

    def _run_async(self, method_name: str, *args, **kwargs):
        import asyncio

        async def runner():
            async with MemoryClient(self.api_key, self.base_url) as client:
                return await getattr(client, method_name)(*args, **kwargs)

        return asyncio.run(runner())

    def _request(self, method: str, path: str, **kwargs) -> dict:
        from nexmem.client import MemoryClient

        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            message = MemoryClient._error_message(response)
            from nexmem.exceptions import NexMemAPIError, NexMemAuthError

            error_cls = NexMemAuthError if response.status_code in {401, 403} else NexMemAPIError
            raise error_cls(response.status_code, message, response)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
