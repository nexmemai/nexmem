import time
import uuid
import logging
from fastapi import Request

logger = logging.getLogger(__name__)


async def logging_middleware(request: Request, call_next):
    """Log all HTTP requests with method, path, status, duration, and request_id."""
    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()

    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.2f}ms)",
        extra={"request_id": request_id}
    )
    response.headers["X-Request-ID"] = request_id
    return response
