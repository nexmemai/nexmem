"""Read-only kill switch (P9-G1).

A single environment-variable flag that an operator can flip to
freeze writes during an incident: a runaway-cost spiral, a
data-corruption bug, a stuck migration. Every state-changing route
returns ``503 Service Unavailable`` with a clear, short message;
read traffic continues to flow normally.

This is *not* a replacement for proper backpressure or rate limits —
it is the on-call engineer's last-resort tool when the rest of the
production guard-rails are not enough. The middleware is
intentionally simple so it can be reasoned about at 3am.

Allowlist:
* Read methods (``GET``, ``HEAD``, ``OPTIONS``) always pass.
* Auth maintenance routes (``/auth/logout``, ``/auth/logout-all``,
  ``/auth/sessions/{id}`` DELETE) always pass — the operator must
  still be able to revoke compromised sessions while the kill
  switch is on.
* The metrics endpoint and health endpoints always pass — flipping
  read-only mode must not also break monitoring and load-balancer
  probes.

Configuration:
* ``READ_ONLY=true`` (env var) or ``settings.read_only=True``
  enables the kill switch. The middleware re-reads ``settings``
  on every request so the flag can be flipped at runtime via
  any path that mutates ``settings`` (env var + reload, or a
  future operator endpoint).
"""

from __future__ import annotations

import json
from typing import Callable

from starlette.types import ASGIApp, Receive, Scope, Send


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Path prefixes that are NEVER frozen, even when read-only is on.
# These are matched against the raw ASGI ``path`` (which begins with
# the route prefix, e.g. ``/api/v1/...``).
_ALWAYS_ALLOWED_PREFIXES: tuple[str, ...] = (
    "/health",
    "/metrics",
)

# Specific (method, path) pairs that bypass the kill switch even
# though the method is unsafe. Order is enforced with simple equality
# / ``startswith`` checks so an operator reading the source can audit
# the allowlist at a glance.
_AUTH_MAINT_PASSTHROUGH: tuple[tuple[str, str], ...] = (
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/logout-all"),
)


class ReadOnlyModeMiddleware:
    """ASGI middleware that 503s every write when the flag is set."""

    def __init__(
        self,
        app: ASGIApp,
        is_read_only: Callable[[], bool],
    ) -> None:
        self.app = app
        self._is_read_only = is_read_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self._is_read_only():
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in _ALWAYS_ALLOWED_PREFIXES):
            await self.app(scope, receive, send)
            return

        for allow_method, allow_path in _AUTH_MAINT_PASSTHROUGH:
            if method == allow_method and path == allow_path:
                await self.app(scope, receive, send)
                return

        # Per-session revocation (DELETE /auth/sessions/{id}) is a
        # variable path; check the prefix explicitly.
        if method == "DELETE" and path.startswith("/api/v1/auth/sessions/"):
            await self.app(scope, receive, send)
            return

        await _send_503(send)


async def _send_503(send: Send) -> None:
    body = json.dumps(
        {
            "detail": (
                "Service is temporarily in read-only mode. Writes are "
                "refused; reads continue. Contact the operator for status."
            ),
            "code": "READ_ONLY_MODE",
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                # Tell well-behaved clients to come back in 60 s.
                (b"retry-after", b"60"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
