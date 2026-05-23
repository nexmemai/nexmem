"""P8-F1 — OpenTelemetry tracing must skip gracefully when not configured.

When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset (the default in the test
suite and in any environment where the operator has not opted in),
the lifespan startup must:

1. Not raise.
2. Not require any opentelemetry-* package to be importable. The
   lazy-import branch in ``app/main.py`` is the contract under test —
   if a future change moves the imports to module scope, this test
   will fail because the OTEL packages are not in the test
   environment.
3. Log a clear ``OpenTelemetry tracing disabled (...)`` line at INFO
   so the operator can confirm from production logs that tracing was
   intentionally off rather than silently broken.
"""
from __future__ import annotations

import logging

import pytest

from app import main as main_module
from app.config import settings
from app.main import app, lifespan


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_otel_skipped_when_endpoint_not_configured(caplog):
    """Lifespan startup must not raise and must log the disabled status."""
    # Sanity: the demo-mode test environment never sets the endpoint.
    assert (
        settings.otel_exporter_otlp_endpoint is None
        or settings.otel_exporter_otlp_endpoint == ""
    ), (
        "Test expected OTEL_EXPORTER_OTLP_ENDPOINT to be unset for the "
        "graceful-skip path; got "
        f"{settings.otel_exporter_otlp_endpoint!r}"
    )

    caplog.set_level(logging.INFO, logger=main_module.logger.name)

    # Entering and exiting the lifespan exercises both the OTEL
    # branch (disabled-path) and the graceful-shutdown teardown.
    async with lifespan(app):
        pass

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "OpenTelemetry tracing disabled" in m for m in messages
    ), (
        "Expected an INFO log line stating tracing is disabled when "
        f"OTEL_EXPORTER_OTLP_ENDPOINT is unset; got: {messages!r}"
    )


async def test_otel_disabled_path_does_not_import_opentelemetry():
    """A pristine demo-mode lifespan must not import the OTEL packages.

    This is what makes the test suite runnable without
    ``opentelemetry-sdk`` installed. If a refactor accidentally moves
    the OTEL imports to module scope or executes them before the
    endpoint check, this test catches it before CI does.
    """
    import sys

    # Take a snapshot of the OTEL-related modules currently loaded.
    snapshot = {name for name in sys.modules if name.startswith("opentelemetry")}

    async with lifespan(app):
        pass

    after = {name for name in sys.modules if name.startswith("opentelemetry")}
    new_modules = after - snapshot

    assert not new_modules, (
        "Lifespan imported opentelemetry packages even though "
        "OTEL_EXPORTER_OTLP_ENDPOINT is unset. Lazy-import contract "
        f"violated; new modules: {sorted(new_modules)!r}"
    )
