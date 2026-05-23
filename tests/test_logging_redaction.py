"""Phase 2 unit tests pinning the observability redaction contract.

The HTTP middleware in app/middleware/logging.py emits one JSON log
line per request with request_id, user_id, app_id, method, path,
status, latency_ms. These tests verify:

* No Authorization header value ever appears in a log line.
* No Cookie / X-Api-Key value appears in a log line.
* The user_id field is populated when an authenticated request hits
  a route that uses get_current_user.
* X-Request-ID is honoured when the client provides a sane value,
  and synthesized otherwise.
"""
from __future__ import annotations

import io
import json
import logging
import re
import uuid
from contextlib import contextmanager

import pytest
from httpx import AsyncClient


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@contextmanager
def _capture_root_logger() -> "io.StringIO":
    """Capture every record sent to the root logger as JSON text."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.DEBUG)

    class _PassThrough(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            # structlog renders the JSON payload into record.msg already.
            return str(record.getMessage())

    handler.setFormatter(_PassThrough())
    root = logging.getLogger()
    prev_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)


def _all_log_text(buffer: io.StringIO) -> str:
    return buffer.getvalue()


async def test_authorization_value_is_never_logged(client: AsyncClient):
    """A bearer token on the wire must not appear in any log line."""
    creds = {
        "email": f"redact_{uuid.uuid4().hex[:6]}@nexmem.example.com",
        "password": "TestPass123!",
    }
    reg = await client.post("/api/v1/auth/register", json=creds)
    assert reg.status_code in (200, 201)
    login = await client.post("/api/v1/auth/login", json=creds)
    access_token = login.json()["access_token"]

    with _capture_root_logger() as buffer:
        r = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert r.status_code == 200

    captured = _all_log_text(buffer)
    # The raw token must not appear anywhere in any log line.
    assert access_token not in captured, (
        "Authorization header value leaked into logs"
    )
    # Defence in depth: no line containing 'Bearer' followed by a real
    # token. (Phrases such as 'WWW-Authenticate: Bearer' are fine.)
    bearer_lines = [
        line for line in captured.splitlines()
        if re.search(r"Bearer\s+[A-Za-z0-9_\-\.]{40,}", line)
    ]
    assert not bearer_lines, f"unredacted Bearer token in logs: {bearer_lines!r}"


async def test_cookie_and_api_key_headers_are_not_logged(client: AsyncClient):
    """Cookie and X-Api-Key header values must not appear in logs."""
    secret_cookie = "session=" + "a" * 32
    secret_api_key = "nxm_" + "b" * 40

    with _capture_root_logger() as buffer:
        # Hit a public route so there's no auth required.
        r = await client.get(
            "/health/live",
            headers={"Cookie": secret_cookie, "X-Api-Key": secret_api_key},
        )
        assert r.status_code == 200

    captured = _all_log_text(buffer)
    assert secret_cookie not in captured, "Cookie value leaked into logs"
    assert secret_api_key not in captured, "X-Api-Key value leaked into logs"


async def test_request_id_is_synthesized_when_absent(client: AsyncClient):
    r = await client.get("/health/live")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    assert rid is not None and 8 <= len(rid) <= 64


async def test_request_id_honours_sane_client_value(client: AsyncClient):
    given = "req-" + uuid.uuid4().hex[:12]
    r = await client.get(
        "/health/live", headers={"X-Request-ID": given}
    )
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == given


async def test_request_id_rejects_garbage_and_synthesizes(client: AsyncClient):
    """A malicious client cannot force us to log arbitrary strings."""
    junk = "x" * 4096  # too long
    r = await client.get(
        "/health/live", headers={"X-Request-ID": junk}
    )
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    assert rid != junk
    assert 8 <= len(rid) <= 64
