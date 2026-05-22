"""Request body size cap middleware (P7-E5).

FastAPI / Starlette accept arbitrarily large request bodies by
default. A 1 GB POST will be buffered into memory before any
pydantic validator runs, which can OOM the worker. This middleware
short-circuits with HTTP 413 ``Payload Too Large`` before the body
is fully consumed.

Two enforcement paths:

1. **Content-Length present.** If the header reports a body larger
   than ``settings.max_request_body_bytes`` we 413 immediately,
   without ever calling ``receive()`` for the body. This is the
   common case for well-behaved clients.

2. **No Content-Length (chunked / streaming).** A malicious client
   can omit Content-Length to bypass an upfront check. We wrap the
   ``receive`` callable so each ``http.request`` event increments a
   running byte counter; once the counter exceeds the cap, we
   inject a synthetic 413 response and stop reading.

The cap is bypassed for ``GET`` / ``HEAD`` / ``OPTIONS`` requests so
the middleware adds no overhead to read traffic.
"""
from __future__ import annotations

import json
from typing import Callable, Union

from starlette.types import ASGIApp, Message, Receive, Scope, Send


_BYPASS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


_MaxBytes = Union[int, Callable[[], int]]


class BodySizeLimitMiddleware:
    """ASGI middleware that 413s any request body above ``max_bytes``.

    ``max_bytes`` may be either an ``int`` or a zero-arg callable. The
    callable form lets the operator change the cap by setting the
    relevant env var and restarting only the process — no redeploy of
    the middleware code is needed — and lets tests monkeypatch the
    setting in-process.
    """

    def __init__(self, app: ASGIApp, max_bytes: _MaxBytes) -> None:
        self.app = app
        self._max_bytes_resolver: _MaxBytes = max_bytes

    @property
    def max_bytes(self) -> int:
        if callable(self._max_bytes_resolver):
            return int(self._max_bytes_resolver())
        return int(self._max_bytes_resolver)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method in _BYPASS_METHODS:
            await self.app(scope, receive, send)
            return

        # Resolve the cap once per request so a config flip mid-deploy
        # is picked up immediately.
        cap = self.max_bytes

        # Fast path: Content-Length is present and over the cap.
        content_length = self._header_int(scope, b"content-length")
        if content_length is not None and content_length > cap:
            await _send_413(send, cap)
            return

        # Slow path: wrap receive so streaming bodies are bounded too.
        wrapper = _ReceiveWrapper(receive, cap, send)
        try:
            await self.app(scope, wrapper.receive, send)
        except _BodyTooLarge:
            # The wrapper has already sent a 413 response; nothing to do.
            return

    @staticmethod
    def _header_int(scope: Scope, name: bytes) -> int | None:
        for key, value in scope.get("headers", ()):
            if key == name:
                try:
                    return int(value)
                except ValueError:
                    return None
        return None


class _BodyTooLarge(Exception):
    """Raised by the receive wrapper after a 413 has been sent."""


class _ReceiveWrapper:
    """Counts body bytes and 413s if the running total exceeds the cap.

    A 413 must be sent *before* the application has produced its own
    response headers, otherwise we'd be writing two responses on the
    same ASGI cycle. To make that easy we keep a flag that is flipped
    once the cap is exceeded; the next ``receive()`` call raises
    ``_BodyTooLarge`` so the application's coroutine unwinds, and we
    have already issued the 413 from inside the receive wrapper.
    """

    def __init__(self, receive: Receive, max_bytes: int, send: Send) -> None:
        self._receive = receive
        self._max = max_bytes
        self._send = send
        self._consumed = 0
        self._tripped = False

    async def receive(self) -> Message:
        if self._tripped:
            raise _BodyTooLarge()
        message = await self._receive()
        if message["type"] != "http.request":
            return message
        body = message.get("body") or b""
        self._consumed += len(body)
        if self._consumed > self._max:
            self._tripped = True
            await _send_413(self._send, self._max)
            raise _BodyTooLarge()
        return message


async def _send_413(send: Send, max_bytes: int) -> None:
    body = json.dumps(
        {
            "detail": (
                f"Request body exceeds the {max_bytes}-byte limit. "
                "Reduce the payload size or split the request."
            )
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
