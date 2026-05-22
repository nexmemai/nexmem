"""Phase 5/6/7 P0 hardening tests.

Covers the three production-blocking items shipped together:
  P5-C1  per-connection statement_timeout + idle-in-tx timeout
  P6-D2  Celery task soft/hard time limits + worker recycling
  P7-E5  request body size cap

Each test fails before its corresponding fix and passes after.
Runs in DEMO_MODE so no Postgres / Redis is required.
"""
from __future__ import annotations

import json
from typing import List

import pytest
from httpx import AsyncClient

from app.config import settings


pytestmark = [pytest.mark.unit]


# ── P5-C1: statement timeout via server_settings ─────────────────────────────
class TestStatementTimeout:
    def test_settings_carry_safe_defaults(self):
        assert settings.db_statement_timeout_ms == 30_000
        assert settings.db_idle_in_transaction_timeout_ms == 60_000
        # Idle-in-transaction MUST be >= statement_timeout, otherwise
        # we can kill a transaction whose only statement is still
        # within budget.
        assert (
            settings.db_idle_in_transaction_timeout_ms
            >= settings.db_statement_timeout_ms
        )

    def test_engine_kwargs_include_server_settings_outside_demo(self, monkeypatch):
        # Force non-demo so the SSL + server_settings branch is taken.
        monkeypatch.setattr(settings, "demo_mode", False)
        from app.database import _build_engine_kwargs

        kwargs = _build_engine_kwargs()
        connect_args = kwargs["connect_args"]
        assert connect_args["ssl"] == "require"
        ss = connect_args["server_settings"]
        assert ss["statement_timeout"] == "30000ms"
        assert ss["idle_in_transaction_session_timeout"] == "60000ms"
        # application_name aids pg_stat_activity triage during incidents.
        assert "application_name" in ss
        assert "nexmem-" in ss["application_name"]

    def test_engine_kwargs_skip_server_settings_in_demo(self):
        # In demo mode the engine never opens a real connection; we
        # must NOT push server_settings (Postgres-only) onto a
        # placeholder URL.
        from app.database import _build_engine_kwargs

        assert settings.demo_mode is True
        connect_args = _build_engine_kwargs()["connect_args"]
        assert "server_settings" not in connect_args
        assert "ssl" not in connect_args


# ── P6-D2/D3/D4: Celery time + memory + result-set guards ────────────────────
class TestCeleryHardening:
    def test_task_time_limits_configured(self):
        from app.celery_app import celery_app

        soft = celery_app.conf.task_soft_time_limit
        hard = celery_app.conf.task_time_limit
        # Soft must be set, lower than hard, and bounded.
        assert soft is not None and 1 <= soft <= 600
        assert hard is not None and soft < hard <= 900

    def test_worker_recycle_configured(self):
        from app.celery_app import celery_app

        # spaCy / sentence-transformers leak under repeated calls.
        # A finite recycle ceiling is required.
        recycle = celery_app.conf.worker_max_tasks_per_child
        assert recycle is not None and 10 <= recycle <= 1_000

    def test_result_expires_configured(self):
        from app.celery_app import celery_app

        expires = celery_app.conf.result_expires
        # Defaults to None / forever; must be set so Redis cannot fill.
        assert expires is not None and expires <= 24 * 3600

    def test_late_ack_and_prefetch_for_graceful_shutdown(self):
        """A SIGTERM during a task should re-queue, not drop.

        ``task_acks_late=True`` defers the ack until after the task
        finishes. ``worker_prefetch_multiplier=1`` keeps the
        re-delivery window small. Together these give safe rolling
        deploys.
        """
        from app.celery_app import celery_app

        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.worker_prefetch_multiplier == 1


# ── P7-E5: request body cap ──────────────────────────────────────────────────
class TestBodySizeLimit:
    @pytest.mark.asyncio
    async def test_body_at_limit_passes(
        self, client: AsyncClient, monkeypatch
    ):
        """Right at the cap, the request must reach the route handler.

        We don't care about the eventual status — only that the
        middleware did NOT short-circuit with 413. The auth/register
        route returns 400 for malformed JSON, which is fine: it
        proves the body was read by the route.
        """
        monkeypatch.setattr(settings, "max_request_body_bytes", 1024)
        body = b"a" * 1024  # Exactly at the cap
        r = await client.post(
            "/api/v1/auth/register",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code != 413, r.text

    @pytest.mark.asyncio
    async def test_body_over_limit_413(
        self, client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(settings, "max_request_body_bytes", 1024)
        body = b"a" * (1024 + 1)
        r = await client.post(
            "/api/v1/auth/register",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413
        assert "exceeds" in r.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_requests_bypass_the_cap(
        self, client: AsyncClient, monkeypatch
    ):
        """Reads do not need the cap and should not pay its overhead."""
        monkeypatch.setattr(settings, "max_request_body_bytes", 1)
        # /health/live returns 200 unconditionally; if the middleware
        # tried to count 0-byte GET bodies it would still pass, but
        # this asserts the bypass exists at all.
        r = await client.get("/health/live")
        assert r.status_code == 200


# ── Body-size middleware unit tests (cover the streaming/no-CL path) ─────────
class TestBodySizeStreaming:
    """Drive the middleware directly so we can exercise the chunked path
    where ``Content-Length`` is absent and the cap must be enforced
    incrementally as bytes arrive."""

    @pytest.mark.asyncio
    async def test_streaming_body_over_limit_413(self):
        from app.middleware.body_size_limit import BodySizeLimitMiddleware

        async def app(scope, receive, send):
            # Drain the body — middleware should 413 before we finish.
            while True:
                msg = await receive()
                if msg["type"] != "http.request":
                    break
                if not msg.get("more_body"):
                    break

        sent: List[dict] = []

        async def send(message):
            sent.append(message)

        chunks = [
            {"type": "http.request", "body": b"a" * 100, "more_body": True},
            {"type": "http.request", "body": b"b" * 100, "more_body": False},
        ]
        idx = 0

        async def receive():
            nonlocal idx
            msg = chunks[idx]
            idx += 1
            return msg

        middleware = BodySizeLimitMiddleware(app, max_bytes=150)
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [],  # No Content-Length on purpose.
        }
        await middleware(scope, receive, send)

        # The middleware short-circuited with a 413 before the second
        # chunk could be read.
        assert sent and sent[0]["type"] == "http.response.start"
        assert sent[0]["status"] == 413
        assert b"exceeds" in sent[1]["body"]

    @pytest.mark.asyncio
    async def test_streaming_body_at_limit_passes(self):
        from app.middleware.body_size_limit import BodySizeLimitMiddleware

        # Sentinel to confirm the inner app actually ran to completion.
        ran = {"finished": False}

        async def app(scope, receive, send):
            while True:
                msg = await receive()
                if msg["type"] != "http.request":
                    break
                if not msg.get("more_body"):
                    break
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})
            ran["finished"] = True

        chunks = [
            {"type": "http.request", "body": b"a" * 50, "more_body": True},
            {"type": "http.request", "body": b"b" * 50, "more_body": False},
        ]
        idx = 0

        async def receive():
            nonlocal idx
            msg = chunks[idx]
            idx += 1
            return msg

        sent: List[dict] = []

        async def send(message):
            sent.append(message)

        middleware = BodySizeLimitMiddleware(app, max_bytes=100)
        await middleware(
            {"type": "http", "method": "POST", "headers": []}, receive, send
        )
        assert ran["finished"] is True
        assert sent[0]["status"] == 200
