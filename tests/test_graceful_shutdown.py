"""Tests for the P9-G2 graceful-shutdown lifespan path.

The contract under test:

1. The lifespan teardown completes without raising in demo mode.
2. After teardown the logger has emitted ``graceful shutdown complete``
   at INFO level so the orchestrator can confirm the drain finished
   before ``terminationGracePeriodSeconds`` elapsed.
3. When in-flight requests exist at teardown time, the lifespan
   blocks for up to ``settings.graceful_shutdown_timeout`` seconds
   waiting for the counter to drain.
4. The DB engine ``dispose()`` call is a no-op in demo mode (no real
   connections to close) and therefore must not raise.

These tests run entirely in demo mode so they do not need a live
Postgres or Redis service.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app import main as main_module
from app.config import settings
from app.main import app, lifespan


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _reset_inflight():
    """Make sure no other test left ``_inflight_count`` non-zero."""
    main_module._inflight_count = 0
    yield
    main_module._inflight_count = 0


async def test_graceful_shutdown_completes_without_error(caplog):
    """Lifespan teardown completes cleanly and logs the shutdown message."""
    caplog.set_level(logging.INFO, logger=main_module.logger.name)

    async with lifespan(app):
        # The body of the context manager is the "running app" phase.
        # We do nothing here — the focus of this test is the teardown.
        pass

    messages = [r.getMessage() for r in caplog.records]
    assert any("graceful shutdown complete" in m for m in messages), (
        "Expected 'graceful shutdown complete' INFO log line; "
        f"got: {messages!r}"
    )


async def test_graceful_shutdown_waits_for_inflight_to_drain():
    """Teardown blocks while in-flight count > 0, returns once it drains."""
    # Pretend one request is in flight when shutdown begins.
    main_module._inflight_count = 1

    async def _drain_after(delay: float) -> None:
        await asyncio.sleep(delay)
        main_module._inflight_count = 0

    drain_task = asyncio.create_task(_drain_after(0.2))

    started = asyncio.get_event_loop().time()
    async with lifespan(app):
        pass
    elapsed = asyncio.get_event_loop().time() - started

    await drain_task

    # Must have waited for the drain (>=0.15s) but well under the
    # configured 30s timeout. The polling interval is 50ms so a small
    # buffer is required to avoid flakiness.
    assert elapsed >= 0.15, (
        f"teardown returned before in-flight drained ({elapsed:.3f}s)"
    )
    assert elapsed < min(settings.graceful_shutdown_timeout, 5), (
        f"teardown took too long ({elapsed:.3f}s); should drain on first poll"
    )
    assert main_module._inflight_count == 0


async def test_graceful_shutdown_gives_up_after_timeout(monkeypatch, caplog):
    """If the in-flight counter never drains, teardown logs a warning
    and returns once the timeout elapses (rather than hanging)."""
    caplog.set_level(logging.WARNING, logger=main_module.logger.name)

    # Force a tiny timeout so the test stays fast.
    monkeypatch.setattr(settings, "graceful_shutdown_timeout", 1)

    main_module._inflight_count = 1  # never drains

    started = asyncio.get_event_loop().time()
    async with lifespan(app):
        pass
    elapsed = asyncio.get_event_loop().time() - started

    # Reset for downstream tests.
    main_module._inflight_count = 0

    # Should have waited ~1 second, then proceeded.
    assert 0.9 <= elapsed < 3.0, (
        f"teardown took {elapsed:.3f}s; expected ~1s timeout"
    )

    warning_messages = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    ]
    assert any(
        "in-flight requests did not finish" in m for m in warning_messages
    ), (
        f"Expected timeout warning; got: {warning_messages!r}"
    )


async def test_graceful_shutdown_zero_inflight_does_not_block(monkeypatch):
    """The fast path: when nothing is in flight, teardown is near-instant."""
    monkeypatch.setattr(settings, "graceful_shutdown_timeout", 30)
    main_module._inflight_count = 0

    started = asyncio.get_event_loop().time()
    async with lifespan(app):
        pass
    elapsed = asyncio.get_event_loop().time() - started

    # No drain needed — should return well under 100ms.
    assert elapsed < 0.5, (
        f"empty-shutdown took {elapsed:.3f}s; expected <0.5s"
    )
