"""Phase 6 P6-D7 circuit breaker tests."""
from __future__ import annotations


import pytest

from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    get_breaker,
    reset_all_breakers,
)


pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _reset_breakers():
    reset_all_breakers()
    yield
    reset_all_breakers()


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(name="t", failure_threshold=3, cooldown_seconds=1)
        assert cb.state() == "CLOSED"
        cb.guard()  # no-op
        cb.record_success()  # no-op

    def test_threshold_failures_open_the_circuit(self):
        cb = CircuitBreaker(name="t", failure_threshold=3, cooldown_seconds=10)
        for _ in range(3):
            cb.record_failure()
        assert cb.state() == "OPEN"
        with pytest.raises(CircuitOpenError):
            cb.guard()

    def test_success_clears_failure_count(self):
        cb = CircuitBreaker(name="t", failure_threshold=3, cooldown_seconds=10)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # Now needs 3 fresh failures to open.
        cb.record_failure()
        cb.record_failure()
        assert cb.state() == "CLOSED"
        cb.record_failure()
        assert cb.state() == "OPEN"

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(name="t", failure_threshold=2, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state() == "OPEN"
        # Force the cooldown to elapse by rewinding the open time.
        with cb._lock:
            cb._opened_at -= 2
        assert cb.state() == "HALF_OPEN"

    def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(name="t", failure_threshold=2, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        with cb._lock:
            cb._opened_at -= 2
        cb.state()  # transition to HALF_OPEN
        cb.record_success()
        assert cb.state() == "CLOSED"

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(name="t", failure_threshold=2, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        with cb._lock:
            cb._opened_at -= 2
        cb.state()  # HALF_OPEN
        cb.record_failure()
        assert cb.state() == "OPEN"

    def test_protect_decorator_records_outcomes(self):
        cb = CircuitBreaker(name="t", failure_threshold=2, cooldown_seconds=10)
        calls = {"n": 0}

        @cb.protect
        def flaky():
            calls["n"] += 1
            raise ValueError("boom")

        # Two failures -> open.
        with pytest.raises(ValueError):
            flaky()
        with pytest.raises(ValueError):
            flaky()
        # Third call short-circuits with CircuitOpenError BEFORE
        # invoking the function.
        before = calls["n"]
        with pytest.raises(CircuitOpenError):
            flaky()
        assert calls["n"] == before

    def test_get_breaker_is_singleton_per_name(self):
        a = get_breaker("alpha")
        b = get_breaker("alpha")
        assert a is b

    def test_reset_clears_state(self):
        cb = CircuitBreaker(name="t", failure_threshold=1, cooldown_seconds=10)
        cb.record_failure()
        assert cb.state() == "OPEN"
        cb.reset()
        assert cb.state() == "CLOSED"
