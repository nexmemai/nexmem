"""Phase 7 P7-E6 JSON shape guard tests."""
from __future__ import annotations

import json
import uuid
from typing import List

import pytest
from httpx import AsyncClient

from app.config import settings
from app.middleware.json_shape_guard import JsonShapeGuardMiddleware


pytestmark = [pytest.mark.unit]


# ── Direct ASGI tests on the middleware ──────────────────────────────────────
class TestMeasure:
    @pytest.mark.asyncio
    async def test_within_limits_passes(self):
        ran = {"reached": False}

        async def app(scope, receive, send):
            # Drain the body that the guard replayed.
            msg = await receive()
            assert msg["type"] == "http.request"
            assert msg["body"] == b'{"a": 1}'
            ran["reached"] = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b"{}"})

        guard = JsonShapeGuardMiddleware(app, max_depth=10, max_nodes=10)
        sent: List[dict] = []
        body = b'{"a": 1}'
        chunks = [{"type": "http.request", "body": body, "more_body": False}]
        idx = 0

        async def receive():
            nonlocal idx
            msg = chunks[idx]
            idx += 1
            return msg

        async def send(m):
            sent.append(m)

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/json")],
        }
        await guard(scope, receive, send)
        assert ran["reached"] is True
        assert sent[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_too_deep_returns_400(self):
        # 50 levels of nesting; cap at 5.
        body_obj = {}
        cur = body_obj
        for _ in range(50):
            cur["x"] = {}
            cur = cur["x"]
        body = json.dumps(body_obj).encode()

        async def inner(scope, receive, send):
            raise AssertionError("should not be reached")

        guard = JsonShapeGuardMiddleware(inner, max_depth=5, max_nodes=10_000)
        sent: List[dict] = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(m):
            sent.append(m)

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/json")],
        }
        await guard(scope, receive, send)
        assert sent[0]["status"] == 400
        assert b"JSON_SHAPE_LIMIT" in sent[1]["body"]

    @pytest.mark.asyncio
    async def test_too_many_nodes_returns_400(self):
        body = json.dumps({"l": list(range(2000))}).encode()

        async def inner(scope, receive, send):
            raise AssertionError("should not be reached")

        guard = JsonShapeGuardMiddleware(inner, max_depth=10, max_nodes=100)
        sent: List[dict] = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(m):
            sent.append(m)

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/json")],
        }
        await guard(scope, receive, send)
        assert sent[0]["status"] == 400

    @pytest.mark.asyncio
    async def test_non_json_passes_through(self):
        ran = {"reached": False}

        async def inner(scope, receive, send):
            ran["reached"] = True

        guard = JsonShapeGuardMiddleware(inner, max_depth=1, max_nodes=1)

        async def receive():
            return {"type": "http.request", "body": b"hello", "more_body": False}

        async def send(_m):
            pass

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"text/plain")],
        }
        await guard(scope, receive, send)
        assert ran["reached"] is True


# ── End-to-end via the app ───────────────────────────────────────────────────
@pytest.mark.asyncio
class TestEndToEnd:
    async def test_register_with_deep_payload_is_400(
        self, client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(settings, "max_request_json_depth", 4)
        deep = {"a": {"a": {"a": {"a": {"a": {"a": "deep"}}}}}}
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": f"x_{uuid.uuid4().hex[:6]}@x.com", "password": "p", "extra": deep},
        )
        assert r.status_code == 400
        assert r.json()["code"] == "JSON_SHAPE_LIMIT"

    async def test_normal_register_still_works(self, client: AsyncClient):
        # The default cap (32 / 10000) easily covers a normal body.
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"shape_ok_{uuid.uuid4().hex[:6]}@nexmem.example.com",
                "password": "TestPass123!",
            },
        )
        assert r.status_code == 201
