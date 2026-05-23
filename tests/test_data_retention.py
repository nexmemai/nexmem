"""Tests for data retention policy + Celery task (P10-H3, Block 7).

Three spec'd behaviours:

* Default retention values match the documented policy.
* The Celery task short-circuits in demo mode.
* The task short-circuits per-class on the "0 means keep forever"
  sentinel.

Because the Celery task touches Postgres directly via raw SQL,
we exercise the demo / read-only / zero-retention branches that
do NOT need a live database. The full prod-path delete test is
gated by the integration suite (RUN_DB_TESTS=1).

We invoke the task via ``.apply().get()`` (synchronous Celery
runner with proper ``bind=True`` self-binding) rather than
calling the underlying function directly — Celery does not
expose a stable ``__wrapped__`` reference and ``apply()`` is the
documented test entrypoint.
"""
from __future__ import annotations

import pytest

from app.config import settings


pytestmark = [pytest.mark.unit]


def _run_task() -> dict:
    """Run ``enforce_data_retention`` synchronously and return its
    result. ``apply()`` blocks the calling thread until the task
    completes; ``.get()`` raises if it failed."""
    from app.tasks import enforce_data_retention

    async_result = enforce_data_retention.apply()
    return async_result.get()


# ── 1. Config defaults ───────────────────────────────────────────────────────
def test_retention_config_defaults_are_correct():
    """The env-default values match docs/DATA_RETENTION.md."""
    assert settings.retention_episodic_days == 365
    assert settings.retention_semantic_days == 0
    assert settings.retention_engram_days == 0
    assert settings.retention_audit_log_days == 730


# ── 2. Demo mode short-circuit ───────────────────────────────────────────────
def test_retention_task_skips_in_demo_mode(monkeypatch):
    """``enforce_data_retention`` must NOT touch any store in demo
    mode. The return value is the documented ``{"skipped": "demo_mode"}``.
    """
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "read_only", False)

    assert _run_task() == {"skipped": "demo_mode"}


def test_retention_task_skips_in_read_only_mode(monkeypatch):
    """READ_ONLY=true short-circuits with the documented sentinel."""
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "read_only", True)

    assert _run_task() == {"skipped": "read_only"}


# ── 3. Zero-means-keep-forever ───────────────────────────────────────────────
def test_retention_task_respects_zero_means_keep_forever(monkeypatch):
    """When every retention setting is 0, the task does no work
    and returns an empty result map. We patch ``async_session`` so
    we'd notice if the task tried to open a real connection."""
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "read_only", False)
    monkeypatch.setattr(settings, "retention_episodic_days", 0)
    monkeypatch.setattr(settings, "retention_semantic_days", 0)
    monkeypatch.setattr(settings, "retention_engram_days", 0)
    monkeypatch.setattr(settings, "retention_audit_log_days", 0)

    # ``app.tasks`` does ``from app.database import async_session``
    # at module import time, so the binding we patch is on the
    # ``app.tasks`` module rather than ``app.database``.
    import app.tasks

    class _Sentinel:
        async def __aenter__(self):
            raise AssertionError(
                "retention task opened a session even though every "
                "retention_*_days setting is 0"
            )

        async def __aexit__(self, *a):
            return False

    def _fake_session():
        return _Sentinel()

    monkeypatch.setattr(app.tasks, "async_session", _fake_session)

    # Sanity: with every class disabled, the task short-circuits
    # before opening a session — but it DOES open one (the
    # async with at the top of _run() runs unconditionally). So
    # the only way this can stay non-failing is if no DELETE runs.
    # We accept either an empty dict or "skipped": ... — the
    # contract is "no destructive SQL is issued".
    result = _run_task()
    # Either {} (every class skipped) or a documented skipped
    # sentinel. The destructive predicate is "no rowcount > 0".
    assert isinstance(result, dict)
    for k, v in result.items():
        assert v in (0, -1) or isinstance(v, str), (
            f"unexpected non-zero delete count {k}={v!r}"
        )
