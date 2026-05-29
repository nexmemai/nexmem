"""HTTP request logging middleware.

Emits one structured log line per request with:

  - request_id   (UUID; also returned via X-Request-ID header)
  - method
  - path
  - route        (FastAPI's path template, e.g. /api/v1/agents/{user_id}/episodes)
  - status_code
  - latency_ms
  - user_id      (when set by user_context_middleware)
  - app_id       (when supplied as a query param or in the request body)

The request_id is bound to a structlog contextvar so any subsequent
log call inside the request handler picks it up automatically.

Sensitive fields (Authorization header, etc.) are NEVER logged; the
log_redactor processor in app.core.logging is a defence-in-depth
scrubber that runs at JSON-render time.
"""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request


_logger = structlog.get_logger("nexmem.http")


async def logging_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    # Best-effort: app_id is sometimes a query param, sometimes in the
    # JSON body. We log the query-param form only — reading the body
    # here is not safe (it has been consumed). Callers that need
    # body-derived app_id can log it themselves from inside the route.
    app_id_q = request.query_params.get("app_id")
    user_id = getattr(getattr(request, "state", None), "current_user_id", None)
    route = getattr(request.scope.get("route"), "path", request.url.path)

    _logger.info(
        "http_request",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        route=route,
        status_code=response.status_code,
        latency_ms=round(duration_ms, 2),
        user_id=user_id,
        app_id=app_id_q,
    )
    response.headers["X-Request-ID"] = request_id
    return response
