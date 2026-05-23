"""P12-J4 (Block 5): MCP server timeout + retry policy.

Three tests that pin down the network policy ``_call_nexmem_api``
applies on every API call:

* ``test_timeout_config_is_set`` — the per-phase httpx timeout matches
  the documented values so a partial regression cannot silently
  loosen the connect / read / write / pool budget.
* ``test_retries_on_transport_error`` — a recoverable network error
  is retried up to 3 times; the underlying transport is invoked 3
  times before the failure surfaces.
* ``test_no_retry_on_4xx_response`` — a server-rejected request is
  NOT retried; one HTTP call, one HTTPStatusError.

Uses ``httpx.MockTransport`` directly (rather than respx) so the
mock layer is entirely inside httpx — no third-party hooks that
could drift between dependency upgrades.

``tenacity.wait_none`` collapses the inter-attempt sleeps so the
retry test runs in sub-second time even though the production
policy waits up to 10 s between attempts.
"""

from __future__ import annotations

import httpx
import pytest
from tenacity import wait_none

from server import NEXMEM_TIMEOUT, _call_nexmem_api


@pytest.fixture(autouse=True)
def _no_retry_sleep():
    """Make every tenacity retry inter-attempt wait collapse to 0.

    Production keeps ``wait_exponential(multiplier=1, min=1, max=10)``;
    tests just need to know the retry count, not the production
    backoff schedule. Patching the decorator's ``retry`` object's
    ``wait`` attribute is the public, supported tenacity API for
    this exact use case.
    """
    original = _call_nexmem_api.retry.wait
    _call_nexmem_api.retry.wait = wait_none()
    try:
        yield
    finally:
        _call_nexmem_api.retry.wait = original


# ── 1 ─────────────────────────────────────────────────────────────────────────
def test_timeout_config_is_set():
    """Pin the per-phase timeout values. If a future refactor
    accidentally drops back to ``timeout=30`` (a single scalar),
    this test fires immediately."""
    assert isinstance(NEXMEM_TIMEOUT, httpx.Timeout)
    assert NEXMEM_TIMEOUT.connect == 5.0
    assert NEXMEM_TIMEOUT.read == 30.0
    assert NEXMEM_TIMEOUT.write == 30.0
    assert NEXMEM_TIMEOUT.pool == 5.0


# ── 2 ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_retries_on_transport_error():
    """A transient transport failure is retried exactly 3 times
    before the last error is raised."""
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        raise httpx.ConnectError("simulated outage", request=request)

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await _call_nexmem_api(
                client, "GET", "https://api.test.example/api/v1/auth/me"
            )
    # Three attempts total — the original plus two retries.
    assert call_count["n"] == 3


# ── 3 ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_retry_on_4xx_response():
    """A 4xx response is a deterministic rejection — retrying it
    would only amplify load. The route must be hit exactly once."""
    call_count = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(404, json={"detail": "no such user"})

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await _call_nexmem_api(
                client, "GET", "https://api.test.example/api/v1/auth/me"
            )
    assert exc.value.response.status_code == 404
    assert call_count["n"] == 1
