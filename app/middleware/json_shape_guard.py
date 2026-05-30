"""Bound the shape of incoming JSON request bodies (P7-E6).

The body cap (P7-E5) bounds raw bytes. Pydantic / ``json.loads`` can
still spend a lot of CPU on a deeply nested or massively-wide
JSON object that fits in 5 MiB. Two examples:

* ``{"a": {"a": {"a": ... 5000 levels deep ...}}}`` — recursive
  parsing makes the stack hot and triggers Python's recursion
  limit, which we then catch as a generic 500.
* ``{"a": [1] * 1_000_000, "b": [1] * 1_000_000, ...}`` — fits in
  the body cap but hammers the JSON parser and the pydantic
  validator.

This middleware reads the (already-cap-bounded) body, parses it
once, walks the structure, and rejects with HTTP 400 if either
``max_request_json_depth`` or ``max_request_json_nodes`` is
exceeded. We then replay the parsed bytes to the inner app so
FastAPI / pydantic does not re-read the request twice.

Order in the stack:

  Body cap (P7-E5)        ← outermost
  Read-only switch (P9-G1)
  JSON shape guard (P7-E6)  ← THIS
  ...

Skipped for:
  * non-JSON content types (the streaming body path / multipart
    uploads / form bodies are out of scope here)
  * GET / HEAD / OPTIONS
  * empty bodies
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Union

from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger(__name__)


_BYPASS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


_MaxInt = Union[int, Callable[[], int]]


class JsonShapeGuardMiddleware:
    """Enforce caps on JSON body depth + total node count."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_depth: _MaxInt,
        max_nodes: _MaxInt,
    ) -> None:
        self.app = app
        self._max_depth_resolver: _MaxInt = max_depth
        self._max_nodes_resolver: _MaxInt = max_nodes

    @property
    def max_depth(self) -> int:
        if callable(self._max_depth_resolver):
            return int(self._max_depth_resolver())
        return int(self._max_depth_resolver)

    @property
    def max_nodes(self) -> int:
        if callable(self._max_nodes_resolver):
            return int(self._max_nodes_resolver())
        return int(self._max_nodes_resolver)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method in _BYPASS_METHODS:
            await self.app(scope, receive, send)
            return

        if not _is_json(scope):
            await self.app(scope, receive, send)
            return

        # Drain the body once. The body cap (P7-E5) has already
        # made this safe to buffer.
        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"") or b""
                more = bool(message.get("more_body", False))
            else:  # http.disconnect or anything else
                more = False

        if not body:
            await self.app(scope, _make_replay(body), send)
            return

        try:
            parsed = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            # Not valid JSON. Hand off to FastAPI / pydantic which
            # produces a clean 422.
            await self.app(scope, _make_replay(body), send)
            return

        depth, nodes = _measure(parsed, self.max_depth, self.max_nodes)
        if depth > self.max_depth or nodes > self.max_nodes:
            await _send_400(send, depth, nodes, self.max_depth, self.max_nodes)
            return

        await self.app(scope, _make_replay(body), send)


def _is_json(scope: Scope) -> bool:
    for key, value in scope.get("headers", ()):
        if key == b"content-type":
            return b"application/json" in value.lower()
    return False


def _measure(value, max_depth: int, max_nodes: int) -> tuple[int, int]:
    """Return (max_depth_observed, total_nodes).

    Iterative walk so a deeply-nested attacker payload can't blow the
    Python stack just by being measured. We short-circuit as soon as
    either limit is exceeded so the worst case is bounded by
    ``max_nodes`` operations.
    """
    stack = [(value, 1)]
    deepest = 1
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            return depth, nodes
        if depth > deepest:
            deepest = depth
            if deepest > max_depth:
                return deepest, nodes
        if isinstance(item, dict):
            for v in item.values():
                stack.append((v, depth + 1))
        elif isinstance(item, list):
            for v in item:
                stack.append((v, depth + 1))
    return deepest, nodes


def _make_replay(body: bytes) -> Callable[[], "asyncio.Future[Message]"]:
    """Return a fake ``receive`` that replays the buffered body once."""
    sent_body = False
    sent_disconnect = False

    async def _receive() -> Message:
        nonlocal sent_body, sent_disconnect
        if not sent_body:
            sent_body = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }
        if not sent_disconnect:
            sent_disconnect = True
            return {"type": "http.disconnect"}
        # Should not happen in a well-formed app, but be safe.
        return {"type": "http.disconnect"}

    return _receive


async def _send_400(
    send: Send,
    depth: int,
    nodes: int,
    max_depth: int,
    max_nodes: int,
) -> None:
    body = json.dumps(
        {
            "detail": (
                f"Request JSON exceeds shape limits "
                f"(depth={depth}/{max_depth}, nodes={nodes}/{max_nodes})."
            ),
            "code": "JSON_SHAPE_LIMIT",
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
