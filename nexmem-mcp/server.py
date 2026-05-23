"""NexMem MCP server."""

from __future__ import annotations

import argparse
import os
from typing import Any
from uuid import uuid4

import httpx
from mcp.server.fastmcp import FastMCP
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tools.recall import recall_tool
from tools.remember import remember_tool
from tools.search import search_tool
from tools.set_profile import set_profile_tool

DEFAULT_BASE_URL = "https://nexmem-api.onrender.com"

mcp = FastMCP("nexmem")
_client: "NexMemAPI | None" = None


# ── P12-J3 (Block 5): MCP server input validation ────────────────────────────
# Per-field maximum lengths. Memory content + tool text bodies are
# capped at 10 000 chars (matches the production API's request body
# guards). Queries are tighter (2 000) — anything beyond that is
# almost certainly a paste error or an attempt at amplification.
# Entity / scope identifiers are tighter still.
_MAX_LEN_TEXT = 10_000
_MAX_LEN_QUERY = 2_000
_MAX_LEN_APP_ID = 100
_MAX_LEN_PROFILE_KEY = 200
INVALID_INPUT_CODE = "INVALID_INPUT"


def validate_input(
    value: Any,
    field: str,
    max_len: int = _MAX_LEN_TEXT,
) -> str:
    """Validate a single string input from a remote MCP client.

    Raises ``ValueError`` on any of:
      * non-string type
      * empty / whitespace-only value
      * length above ``max_len``
      * embedded null byte (``\\x00``) — these break Postgres ``text``
        and are a cheap fingerprint of malformed clients.

    On success returns ``value.strip()`` so trailing whitespace cannot
    sneak past the cap by inflating the apparent length post-trim.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value or not value.strip():
        raise ValueError(f"{field} cannot be empty or whitespace")
    if len(value) > max_len:
        raise ValueError(
            f"{field} exceeds maximum length of {max_len} characters"
        )
    if "\x00" in value:
        raise ValueError(f"{field} contains invalid null bytes")
    return value.strip()


def _invalid_input_response(exc: ValueError) -> dict[str, Any]:
    """Uniform error envelope every tool handler returns on a bad
    input. The MCP client can pattern-match on ``code`` to decide
    whether to retry with a fixed payload or surface to the user."""
    return {"error": str(exc), "code": INVALID_INPUT_CODE}


def get_api_key(args_api_key: str | None = None) -> str:
    """Get API key: CLI arg > env var > error."""
    if args_api_key:
        return args_api_key
    env_key = os.environ.get("NEXMEM_API_KEY")
    if env_key:
        return env_key
    raise ValueError("API key required: pass --api-key or set NEXMEM_API_KEY env var")


class NexMemAPI:
    """Small async HTTP client for NexMem API routes used by MCP tools."""

    def __init__(self, api_key: str, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._user_id: str | None = None
        # P12-J4 (Block 5): per-phase timeout. ``connect`` and ``pool``
        # are tight (5 s) — slow DNS or saturated pool is a fast fail.
        # ``read`` and ``write`` are 30 s because RAG / memory writes
        # legitimately wait on OpenAI completions or embedder batches.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=NEXMEM_TIMEOUT,
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
        """Issue one API call. Retries are owned by ``_call_nexmem_api``.

        ``_call_nexmem_api`` retries up to 3 times on
        ``httpx.TransportError`` (DNS failure, connection refused,
        read timeout, etc.) and reraises the last error if every
        attempt fails. 4xx / 5xx responses come back as
        ``HTTPStatusError`` and are NOT retried — they almost always
        mean the request is wrong, not the network, and a retry
        amplifies the problem.
        """
        try:
            response = await _call_nexmem_api(self._client, method, path, **kwargs)
        except httpx.HTTPStatusError as exc:
            # Re-wrap with the user-facing message the previous version
            # used so callers that match on the string keep working.
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, AttributeError):
                detail = exc.response.text
            raise httpx.HTTPStatusError(
                f"NexMem API error {exc.response.status_code}: {detail}",
                request=exc.response.request,
                response=exc.response,
            ) from exc

        if not response.content:
            return {}
        return response.json()


# ── P12-J4 (Block 5): timeout + retry policy ─────────────────────────────────
# Per-phase timeout. ``connect`` failure is fast (5 s) so we don't hold
# a worker waiting on a dead DNS / TCP. ``read``/``write`` are 30 s
# because RAG and memory-write paths legitimately wait on OpenAI or
# the embedder. ``pool`` 5 s caps the wait for a free httpx connection.
NEXMEM_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


# Total per-operation budget worst case:
#   3 attempts × 30 s read + 2 × 10 s exponential wait + 5 s connect
#   ≈ 95 s. That is acceptable for MCP tool calls because each call
#   already runs in a dedicated subprocess with no per-tool deadline
#   tighter than the user's stop-button. The retry is ONLY on
#   ``TransportError`` so a 4xx (e.g. invalid input rejected by the
#   API) bypasses retries entirely and surfaces immediately.
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)
async def _call_nexmem_api(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Central HTTP caller used by every NexMemAPI method.

    Wraps ``client.request`` with the per-phase NEXMEM_TIMEOUT and a
    tenacity retry that reraises after 3 attempts. Calls
    ``response.raise_for_status()`` so any 4xx / 5xx is surfaced as
    an ``HTTPStatusError`` rather than a 200-shaped error envelope.
    """
    response = await client.request(method, url, timeout=NEXMEM_TIMEOUT, **kwargs)
    response.raise_for_status()
    return response


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
    try:
        text = validate_input(text, "text", max_len=_MAX_LEN_TEXT)
        if app_id is not None:
            app_id = validate_input(app_id, "app_id", max_len=_MAX_LEN_APP_ID)
    except ValueError as exc:
        return _invalid_input_response(exc)
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
    try:
        query = validate_input(query, "query", max_len=_MAX_LEN_QUERY)
        if app_id is not None:
            app_id = validate_input(app_id, "app_id", max_len=_MAX_LEN_APP_ID)
    except ValueError as exc:
        return _invalid_input_response(exc)
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
    try:
        key = validate_input(key, "key", max_len=_MAX_LEN_PROFILE_KEY)
        if app_id is not None:
            app_id = validate_input(app_id, "app_id", max_len=_MAX_LEN_APP_ID)
    except ValueError as exc:
        return _invalid_input_response(exc)
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
    try:
        query = validate_input(query, "query", max_len=_MAX_LEN_QUERY)
        if app_id is not None:
            app_id = validate_input(app_id, "app_id", max_len=_MAX_LEN_APP_ID)
    except ValueError as exc:
        return _invalid_input_response(exc)
    data = await get_client().recall(query=query, limit=limit, app_id=app_id)
    return {
        "query": query,
        "semantic_hits": data.get("semantic_hits", []),
        "recent_episodes": data.get("recent_episodes", []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NexMem MCP server")
    parser.add_argument("--api-key", required=True, help="NexMem API key, e.g. nxm_xxxxx")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"NexMem API base URL. Defaults to {DEFAULT_BASE_URL}",
    )
    return parser.parse_args()


def main() -> None:
    global _client
    args = parse_args()
    api_key = get_api_key(args.api_key)
    _client = NexMemAPI(api_key=api_key, base_url=args.base_url)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
