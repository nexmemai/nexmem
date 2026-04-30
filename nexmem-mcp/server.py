"""NexMem MCP server."""

from __future__ import annotations

import argparse
from typing import Any
from uuid import uuid4

import httpx
from mcp.server.fastmcp import FastMCP

from tools.recall import recall_tool
from tools.remember import remember_tool
from tools.search import search_tool
from tools.set_profile import set_profile_tool

DEFAULT_BASE_URL = "https://nexmem-api.onrender.com"

mcp = FastMCP("nexmem")
_client: "NexMemAPI | None" = None


class NexMemAPI:
    """Small async HTTP client for NexMem API routes used by MCP tools."""

    def __init__(self, api_key: str, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._user_id: str | None = None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30,
            headers={
                "Authorization": f"ApiKey {api_key}",
                "Accept": "application/json",
                "User-Agent": "nexmem-mcp/0.1.0",
            },
        )

    async def remember(
        self,
        text: str,
        app_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/memory/episode/write",
            json={
                "content": text,
                "session_id": str(uuid4()),
                "app_id": app_id,
                "metadata": metadata or {},
                "tags": [],
            },
        )

    async def recall(
        self,
        query: str,
        limit: int = 5,
        app_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/memory/context",
            json={
                "query": query,
                "semantic_top_k": limit,
                "episodic_limit": limit,
                "app_id": app_id,
            },
        )

    async def get_profile(self, app_id: str | None = None) -> dict[str, Any]:
        user_id = await self.get_user_id()
        params = {"app_id": app_id} if app_id else None
        try:
            data = await self._request(
                "GET",
                f"/api/v1/agents/{user_id}/procedural/settings",
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {}
            raise
        return data.get("settings", {})

    async def set_profile(
        self,
        key: str,
        value: Any,
        app_id: str | None = None,
    ) -> dict[str, Any]:
        user_id = await self.get_user_id()
        profile = await self.get_profile(app_id=app_id)
        profile[key] = value
        params = {"app_id": app_id} if app_id else None
        return await self._request(
            "POST",
            f"/api/v1/agents/{user_id}/procedural/settings",
            params=params,
            json={"settings": profile, "workflows": []},
        )

    async def get_user_id(self) -> str:
        if self._user_id is None:
            data = await self._request("GET", "/api/v1/auth/me")
            user_id = data.get("id")
            if not isinstance(user_id, str):
                raise RuntimeError("NexMem API did not return an authenticated user id")
            self._user_id = user_id
        return self._user_id

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise httpx.HTTPStatusError(
                f"NexMem API error {response.status_code}: {detail}",
                request=response.request,
                response=response,
            )
        if not response.content:
            return {}
        return response.json()


def get_client() -> NexMemAPI:
    if _client is None:
        raise RuntimeError("NexMem MCP server is not configured")
    return _client


@mcp.tool(
    name=remember_tool["name"],
    description=remember_tool["description"],
)
async def nexmem_remember(
    text: str,
    app_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store durable long-term memory."""
    return await get_client().remember(text=text, app_id=app_id, metadata=metadata)


@mcp.tool(
    name=recall_tool["name"],
    description=recall_tool["description"],
)
async def nexmem_recall(
    query: str,
    limit: int = 5,
    app_id: str | None = None,
) -> dict[str, Any]:
    """Recall composed context from NexMem."""
    data = await get_client().recall(query=query, limit=limit, app_id=app_id)
    return {
        "content": data.get("assembled_context", ""),
        "semantic_hits": data.get("semantic_hits", []),
        "recent_episodes": data.get("recent_episodes", []),
        "preferences": data.get("preferences", {}),
        "graph_context": data.get("graph_context", {}),
        "metadata": data.get("metadata", {}),
    }


@mcp.tool(
    name=set_profile_tool["name"],
    description=set_profile_tool["description"],
)
async def nexmem_set_profile(
    key: str,
    value: Any,
    app_id: str | None = None,
) -> dict[str, Any]:
    """Set a durable profile key."""
    await get_client().set_profile(key=key, value=value, app_id=app_id)
    return {"updated": True, "key": key, "value": value}


@mcp.tool(
    name=search_tool["name"],
    description=search_tool["description"],
)
async def nexmem_search(
    query: str,
    limit: int = 5,
    app_id: str | None = None,
) -> dict[str, Any]:
    """Search memory snippets."""
    data = await get_client().recall(query=query, limit=limit, app_id=app_id)
    return {
        "query": query,
        "semantic_hits": data.get("semantic_hits", []),
        "recent_episodes": data.get("recent_episodes", []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NexMem MCP server")
    parser.add_argument("--api-key", required=True, help="NexMem API key, e.g. mem_xxxxx")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"NexMem API base URL. Defaults to {DEFAULT_BASE_URL}",
    )
    return parser.parse_args()


def main() -> None:
    global _client
    args = parse_args()
    _client = NexMemAPI(api_key=args.api_key, base_url=args.base_url)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
