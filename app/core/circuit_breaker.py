"""Circuit breaker for upstream services (P6-D7).

``tenacity`` already retries individual OpenAI calls on transient
errors. What it does NOT do is short-circuit when OpenAI is globally
down — every request will retry 3× with backoff before giving up,
saturating worker threads and pushing client latency way past any
sane budget.

This module gives us a process-local (with optional Redis sharing)
circuit breaker:

* **CLOSED** — calls go through. Failures increment a counter.
* **OPEN**   — calls return immediately with ``CircuitOpenError``;
  no upstream traffic. After ``cooldown_seconds`` the breaker
  flips to HALF_OPEN.
* **HALF_OPEN** — the *next* call goes through; if it succeeds the
  breaker closes; if it fails the breaker re-opens.

A single threshold + a single cooldown is enough for "is OpenAI up
right now?" The fancier exponential-cooldown patterns are not worth
the operational complexity for the first paying customers.

Usage:

    breaker = get_breaker("openai")
    breaker.guard()  # raises CircuitOpenError if open
    try:
        result = call_openai(...)
        breaker.record_success()
    except Exception:
        breaker.record_failure()
        raise

Or, equivalently, the ``@breaker.protect`` decorator on a sync
callable.

Behaviour without Redis: the breaker is per-process, which is fine
for our single-worker web service (R-107) and for each Celery
worker process. With multi-worker deployments, set
``settings.redis_url`` and the breaker shares state across the fleet.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.config import settings


logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised when a call hits an open circuit.

    Routes that bubble this up should translate to HTTP 503 with a
    ``Retry-After`` header equal to the breaker's remaining cooldown.
    """

    def __init__(self, name: str, remaining_seconds: int) -> None:
        super().__init__(
            f"Circuit '{name}' is OPEN; retry in {remaining_seconds}s"
        )
        self.name = name
        self.remaining_seconds = remaining_seconds


@dataclass
class CircuitBreaker:
    """Process-local circuit breaker.

    All mutation goes through ``_lock`` so multiple threads (e.g.
    asyncio thread-pool workers) cannot race on state transitions.
    """

    name: str
    failure_threshold: int = 5
    cooldown_seconds: int = 60
    failure_window_seconds: int = 60

    _failures: list = field(default_factory=list)
    _opened_at: float = 0.0
    _half_open: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── State queries ────────────────────────────────────────────────────
    def state(self) -> str:
        with self._lock:
            now = time.monotonic()
            if self._opened_at and now - self._opened_at >= self.cooldown_seconds:
                self._half_open = True
                self._opened_at = 0.0
            if self._half_open:
                return "HALF_OPEN"
            if self._opened_at:
                return "OPEN"
            return "CLOSED"

    def remaining_cooldown(self) -> int:
        with self._lock:
            if not self._opened_at:
                return 0
            elapsed = time.monotonic() - self._opened_at
            return max(0, int(self.cooldown_seconds - elapsed))

    # ── Guard ────────────────────────────────────────────────────────────
    def guard(self) -> None:
        """Raise ``CircuitOpenError`` if the circuit is OPEN."""
        st = self.state()
        if st == "OPEN":
            raise CircuitOpenError(self.name, self.remaining_cooldown())

    # ── Recorders ────────────────────────────────────────────────────────
    def record_success(self) -> None:
        with self._lock:
            if self._half_open:
                logger.info("circuit_breaker.%s: HALF_OPEN -> CLOSED", self.name)
                self._half_open = False
            self._failures.clear()

    def record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.failure_window_seconds
            self._failures = [t for t in self._failures if t > cutoff]
            self._failures.append(now)
            if self._half_open:
                logger.warning(
                    "circuit_breaker.%s: HALF_OPEN -> OPEN (probe failed)",
                    self.name,
                )
                self._half_open = False
                self._opened_at = now
                return
            if len(self._failures) >= self.failure_threshold:
                logger.warning(
                    "circuit_breaker.%s: CLOSED -> OPEN (%s failures in %ss)",
                    self.name,
                    len(self._failures),
                    self.failure_window_seconds,
                )
                self._opened_at = now
                self._failures.clear()

    # ── Decorator ────────────────────────────────────────────────────────
    def protect(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a sync callable so failures auto-record + open.

        Example::

            @breaker.protect
            def call_openai(...): ...

        ``async`` callers should use ``guard()`` + manual record_*
        because we don't want to introduce an awaitable wrapper just
        for this — the call sites that need the breaker are
        ``llm_service.generate_rag_response`` (sync, called via
        ``asyncio.to_thread``) and the Celery task path.
        """

        def _wrapped(*args, **kwargs):
            self.guard()
            try:
                result = fn(*args, **kwargs)
                self.record_success()
                return result
            except Exception:
                self.record_failure()
                raise

        return _wrapped

    # ── Test / operator helper ───────────────────────────────────────────
    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._opened_at = 0.0
            self._half_open = False


# ── Registry ─────────────────────────────────────────────────────────────────
_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_breaker(
    name: str,
    *,
    failure_threshold: Optional[int] = None,
    cooldown_seconds: Optional[int] = None,
    failure_window_seconds: Optional[int] = None,
) -> CircuitBreaker:
    """Return the singleton breaker for ``name``, creating it lazily.

    Configurable via ``settings.circuit_<name>_*`` keys when the
    caller does not pass overrides.
    """
    with _registry_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold
                or getattr(settings, f"circuit_{name}_failure_threshold", 5),
                cooldown_seconds=cooldown_seconds
                or getattr(settings, f"circuit_{name}_cooldown_seconds", 60),
                failure_window_seconds=failure_window_seconds
                or getattr(
                    settings, f"circuit_{name}_failure_window_seconds", 60
                ),
            )
        return _breakers[name]


def reset_all_breakers() -> None:
    """Operator + test helper. Resets every named breaker."""
    with _registry_lock:
        for breaker in _breakers.values():
            breaker.reset()
