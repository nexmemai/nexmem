"""P8-F7: ``/health/ready`` includes Celery."""
from __future__ import annotations

import pytest

from app.config import settings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class TestCeleryProbe:
    async def test_probe_skipped_in_demo(self):
        from app.routers.health import _probe_celery

        assert settings.demo_mode is True
        assert (await _probe_celery()).startswith("skipped")

    async def test_probe_skipped_when_no_redis(self, monkeypatch):
        # Force non-demo so the `if demo_mode:` short-circuit doesn't fire.
        monkeypatch.setattr(settings, "demo_mode", False)
        monkeypatch.setattr(settings, "redis_url", None)
        from app.routers.health import _probe_celery

        result = await _probe_celery()
        assert result.startswith("skipped")
        assert "REDIS_URL" in result

    async def test_probe_reports_no_workers(self, monkeypatch):
        monkeypatch.setattr(settings, "demo_mode", False)
        monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")

        # Patch celery_app's inspect() so .ping() returns None
        # (that is, "broker reachable but no workers").
        from app import celery_app as _ca

        class _FakeInspect:
            def __init__(self, *a, **kw): ...

            def ping(self):
                return None

        class _FakeControl:
            def inspect(self, **kw):
                return _FakeInspect()

        monkeypatch.setattr(_ca.celery_app, "control", _FakeControl())

        from app.routers.health import _probe_celery

        result = await _probe_celery()
        assert result.startswith("error: no Celery workers")

    async def test_probe_ok_when_workers_respond(self, monkeypatch):
        monkeypatch.setattr(settings, "demo_mode", False)
        monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")

        from app import celery_app as _ca

        class _FakeInspect:
            def __init__(self, *a, **kw): ...

            def ping(self):
                return {"celery@host-1": "pong"}

        class _FakeControl:
            def inspect(self, **kw):
                return _FakeInspect()

        monkeypatch.setattr(_ca.celery_app, "control", _FakeControl())

        from app.routers.health import _probe_celery

        assert await _probe_celery() == "ok"


class TestReadyEndpointShape:
    async def test_celery_key_present(self, client):
        r = await client.get("/health/ready")
        body = r.json()
        assert "celery" in body["checks"]
