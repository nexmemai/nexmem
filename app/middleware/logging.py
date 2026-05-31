"""HTTP request logging middleware.

Phase 2 (R-110):
* Every request emits a single structured JSON log line via structlog
  with: ``request_id``, ``user_id``, ``app_id``, ``method``, ``path``,
  ``status``, ``latency_ms``, plus ``client_ip`` and ``user_agent``
  for incident response.
* Authorization, Cookie, X-Api-Key, Set-Cookie headers and any field
  named like a credential are NEVER logged. The redaction list is
  shared with Sentry's before_send hook in app/main.py.
* The X-Request-ID response header is preserved so client SDKs can
  correlate failures to log lines.
* If the upstream client passes an ``X-Request-ID`` header, it is
  honoured (with a length cap so we cannot be forced to log
  attacker-controlled strings of arbitrary size).
"""
from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request

logger = structlog.get_logger("nexmem.http")


_HEADER_DENYLIST = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "proxy-authorization",
}


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _request_id(request: Request) -> str:
    incoming = (request.headers.get("X-Request-ID") or "").strip()
    if 8 <= len(incoming) <= 64 and all(
        c.isalnum() or c in "-_" for c in incoming
    ):
        return incoming
    return uuid.uuid4().hex[:16]


async def logging_middleware(request: Request, call_next):
    request_id = _request_id(request)
    request.state.request_id = request_id
    start = time.perf_counter()

    user_id: str | None = None
    app_id: str | None = None

    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "http_request_failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            user_id=getattr(request.state, "current_user_id", None),
            app_id=getattr(request.state, "current_app_id", None),
            client_ip=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
            latency_ms=round(latency_ms, 2),
        )
        raise

    user_id = getattr(request.state, "current_user_id", None)
    app_id = getattr(request.state, "current_app_id", None)
    latency_ms = (time.perf_counter() - start) * 1000

    logger.info(
        "http_request",
        request_id=request_id,
        user_id=user_id,
        app_id=app_id,
        method=request.method,
        path=request.url.path,
        status=status,
        latency_ms=round(latency_ms, 2),
        client_ip=_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )

    response.headers["X-Request-ID"] = request_id
    return response
