"""Phase 6 Celery hardening tests.

Covers:
  P6-D1  real Dead Letter Queue (Redis list)
  P6-D5  per-(user, window) idempotency lock
  P6-D6  NLP/LLM precompute happens OUTSIDE any DB transaction
  P6-D9  RLS context is set on the Celery task's session
  P9-G1  Celery tasks honour the read-only kill switch
"""
from __future__ import annotations

import inspect
import json
import uuid
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings


pytestmark = [pytest.mark.unit]


# ── In-memory Redis stand-in ─────────────────────────────────────────────────
class _FakeRedis:
    """Tiny subset of the redis client that the lock + DLQ helpers use.

    Implements ``set(... nx=True, ex=...)``, ``eval`` with the release
    Lua script, ``lpush``, ``ltrim``, ``lrange``, ``llen``. Enough to
    drive the celery_locks helpers end-to-end without the actual
    network dependency, so the unit suite stays hermetic.
    """

    def __init__(self) -> None:
        self.kv: Dict[str, str] = {}
        self.lists: Dict[str, List[str]] = {}

    # ── string ops (lock) ────────────────────────────────────────────────
    def set(self, name, value, nx=False, ex=None):  # noqa: ANN001
        if nx and name in self.kv:
            return None
        self.kv[name] = value
        return True

    def get(self, name):  # noqa: ANN001
        return self.kv.get(name)

    def delete(self, name):  # noqa: ANN001
        return 1 if self.kv.pop(name, None) is not None else 0

    def eval(self, script, numkeys, key, arg):  # noqa: ANN001
        # Implements the release_lock Lua: del key only if value matches.
        if self.kv.get(key) == arg:
            self.kv.pop(key, None)
            return 1
        return 0

    # ── list ops (DLQ) ───────────────────────────────────────────────────
    def lpush(self, name, value):  # noqa: ANN001
        self.lists.setdefault(name, []).insert(0, value)
        return len(self.lists[name])

    def ltrim(self, name, start, end):  # noqa: ANN001
        if name not in self.lists:
            return True
        if end < 0:
            end = len(self.lists[name]) + end
        self.lists[name] = self.lists[name][start : end + 1]
        return True

    def lrange(self, name, start, end):  # noqa: ANN001
        items = self.lists.get(name, [])
        if end < 0:
            end = len(items) + end
        return items[start : end + 1]

    def llen(self, name):  # noqa: ANN001
        return len(self.lists.get(name, []))


@pytest.fixture
def fake_redis(monkeypatch):
    """Make ``app.core.celery_locks._redis_client`` return our fake."""
    fake = _FakeRedis()
    # Force the helper to think Redis is configured even in DEMO_MODE,
    # then short-circuit to the fake client.
    monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")
    from app.core import celery_locks

    monkeypatch.setattr(celery_locks, "_redis_client", lambda: fake)
    return fake


# ── P6-D5: idempotency lock ──────────────────────────────────────────────────
class TestIdempotencyLock:
    def test_first_acquire_wins_second_loses(self, fake_redis):
        from app.core.celery_locks import acquire_lock

        token1 = acquire_lock("k1", ttl_seconds=60)
        token2 = acquire_lock("k1", ttl_seconds=60)
        assert token1 and token1 != "no-redis"
        assert token2 is None

    def test_release_only_with_matching_token(self, fake_redis):
        from app.core.celery_locks import acquire_lock, release_lock

        token = acquire_lock("k2", ttl_seconds=60)
        assert release_lock("k2", "wrong-token") is False
        assert release_lock("k2", token) is True
        # After release, the lock can be re-acquired.
        again = acquire_lock("k2", ttl_seconds=60)
        assert again and again != "no-redis"

    def test_no_redis_fails_open(self, monkeypatch):
        # No fake_redis fixture → the helper has no client.
        monkeypatch.setattr(settings, "redis_url", None)
        from app.core import celery_locks

        monkeypatch.setattr(celery_locks, "_redis_client", lambda: None)
        token = celery_locks.acquire_lock("k3", ttl_seconds=60)
        # Fail-open: caller proceeds. The structured logger records the
        # outage; this test only asserts the contract.
        assert token == "no-redis"


# ── P6-D1: dead-letter queue ─────────────────────────────────────────────────
class TestDLQ:
    def test_push_lands_in_redis_list(self, fake_redis):
        from app.core.celery_locks import dlq_peek, dlq_push

        ok = dlq_push({"user_id": "u-1", "error": "boom"})
        assert ok is True
        items = dlq_peek()
        assert len(items) == 1
        assert items[0]["user_id"] == "u-1"
        assert items[0]["error"] == "boom"
        assert "dlq_at" in items[0]

    def test_push_trims_to_max(self, fake_redis, monkeypatch):
        # Lower the cap so we don't have to push 1000 entries.
        monkeypatch.setattr(settings, "dlq_max_entries", 3)
        from app.core.celery_locks import dlq_peek, dlq_push

        for i in range(5):
            dlq_push({"i": i})
        items = dlq_peek(limit=10)
        # Newest first; oldest 2 trimmed.
        assert [it["i"] for it in items] == [4, 3, 2]

    def test_push_falls_back_to_logs_when_no_redis(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "redis_url", None)
        from app.core import celery_locks

        monkeypatch.setattr(celery_locks, "_redis_client", lambda: None)
        with caplog.at_level("CRITICAL"):
            ok = celery_locks.dlq_push({"user_id": "u-2", "error": "no-redis"})
        assert ok is False
        # The CRITICAL log line carries the payload so an operator can
        # recover from log archives even when Redis is down.
        assert any(
            "DLQ-FALLBACK" in r.message and "u-2" in r.message
            for r in caplog.records
        )


# ── P6-D6: NLP/LLM outside DB transaction (source-level guarantees) ──────────
class TestNoTransactionDuringPrecompute:
    """The pipeline must not hold a Postgres transaction across LLM /
    NLP calls. We assert via three signals:

    1. Source contains an explicit transaction-close before precompute.
    2. ``_precompute_episode`` does not take a session argument
       (function signature does not include ``db`` / ``session``).
    3. The write phase opens its own ``async with db.begin():`` block.

    All three must hold for the regression to stay closed.
    """

    def test_consolidate_for_user_closes_tx_before_precompute(self):
        from app.services.consolidation import consolidate_for_user

        src = inspect.getsource(consolidate_for_user)
        # The read-then-commit pattern must be visible.
        assert "if db.in_transaction():" in src
        assert "await db.commit()" in src
        # Snapshots are taken before precompute.
        assert "_EpisodeSnapshot" in src

    def test_precompute_episode_takes_no_session(self):
        from app.services.consolidation import _precompute_episode

        sig = inspect.signature(_precompute_episode)
        params = set(sig.parameters)
        assert "db" not in params
        assert "session" not in params

    def test_write_phase_uses_explicit_transaction_block(self):
        from app.services.consolidation import _write_consolidated

        src = inspect.getsource(_write_consolidated)
        assert "async with db.begin()" in src


# ── P6-D9: RLS context bound on the Celery session ───────────────────────────
class TestCeleryRLSContext:
    """The task source must call ``set_rls_context`` after opening a
    session. Without this, RLS forced + no context returns zero rows
    in production, which is a silent correctness bug.
    """

    def test_consolidate_user_memory_task_sets_rls(self):
        from app.tasks import consolidate_user_memory_task

        src = inspect.getsource(consolidate_user_memory_task)
        assert "set_rls_context(db, user_id)" in src

    def test_consolidate_all_users_does_not_set_rls_for_enumeration(self):
        """Enumerating all users requires the lookup policy
        (current_user IS NULL); the task must NOT bind a per-user
        context for the SELECT user.id step."""
        from app.tasks import consolidate_all_users

        src = inspect.getsource(consolidate_all_users)
        # Allow an explanatory comment that mentions the helper, but
        # disallow an actual call to it inside the enumeration block.
        # Cheap heuristic: the literal call ``set_rls_context(`` must
        # not appear in the source.
        assert "set_rls_context(" not in src


# ── End-to-end task wiring (P6-D5 + P6-D9 integration) ───────────────────────
class TestTaskWiring:
    """Drive the task synchronously and assert its observable contract."""

    def test_lock_held_when_task_runs(self, fake_redis, monkeypatch):
        """If a duplicate is already in flight, the second invocation
        short-circuits with skipped=duplicate and never reaches the
        consolidation pipeline."""
        from app.core.celery_locks import acquire_lock
        from app.tasks import (
            _consolidation_lock_key,
            consolidate_user_memory_task,
        )

        user_id = str(uuid.uuid4())
        # Pre-acquire the lock as if another worker holds it.
        held = acquire_lock(_consolidation_lock_key(user_id, 1), 60)
        assert held

        # Patch the consolidation pipeline so we can detect whether
        # the task ever reached it.
        called = {"n": 0}

        async def _never_called(*a, **kw):
            called["n"] += 1
            return 0

        monkeypatch.setattr(
            "app.tasks.consolidate_for_user", _never_called
        )

        # Run the underlying function (Celery's binding wrapping).
        # We bypass .delay()/Celery and call .run() directly with a
        # fake ``self`` so the test stays in-process.
        result = _run_celery_task(
            consolidate_user_memory_task,
            args=(user_id,),
            kwargs={"days_old": 1},
        )

        assert result == {"skipped": True, "reason": "duplicate"}
        assert called["n"] == 0

    def test_read_only_skips_immediately(self, monkeypatch):
        from app.tasks import consolidate_user_memory_task

        monkeypatch.setattr(settings, "read_only", True)
        result = _run_celery_task(
            consolidate_user_memory_task,
            args=("any-user",),
            kwargs={},
        )
        assert result == {"skipped": True, "reason": "read_only"}

    def test_failure_after_max_retries_lands_in_dlq(
        self, fake_redis, monkeypatch
    ):
        """Simulate ``self.request.retries == max_retries`` + a raise.
        The task must call ``dlq_push`` exactly once with a payload
        that includes the user_id and the error string."""
        from app.tasks import consolidate_user_memory_task

        async def _boom(*a, **kw):
            raise RuntimeError("openai down")

        monkeypatch.setattr("app.tasks.consolidate_for_user", _boom)
        # The task path also opens a session and sets RLS context
        # before reaching ``consolidate_for_user``. Both must be
        # neutralised so the test exercises only the failure handler.
        _patch_session(monkeypatch)

        result = _run_celery_task(
            consolidate_user_memory_task,
            args=("dlq-user",),
            kwargs={},
            request_overrides={"retries": 3, "id": "task-abc"},
            max_retries_override=3,
        )

        assert result == {
            "failed": True,
            "user_id": "dlq-user",
            "dlq": True,
        }
        from app.core.celery_locks import dlq_peek

        items = dlq_peek()
        assert items
        latest = items[0]
        assert latest["user_id"] == "dlq-user"
        assert "openai down" in latest["error"]
        assert latest["error_type"] == "RuntimeError"
        assert latest["task_id"] == "task-abc"


# ── helpers ──────────────────────────────────────────────────────────────────
def _patch_session(monkeypatch):
    """Replace the task's session + RLS plumbing with no-ops.

    The task body is a thin wrapper around ``consolidate_for_user``.
    For the DLQ test we only care about the failure-handling path,
    so we short-circuit the I/O.
    """
    class _DummyAsyncCM:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.tasks.async_session", lambda: _DummyAsyncCM())

    async def _no_rls(_db, _uid):
        return None

    monkeypatch.setattr("app.tasks.set_rls_context", _no_rls)


def _run_celery_task(
    task,
    *,
    args: tuple,
    kwargs: dict,
    request_overrides: Dict[str, Any] | None = None,
    max_retries_override: int | None = None,
) -> Any:
    """Invoke a bound Celery task without going through the broker.

    ``task.run`` is a bound method on the Celery task class. Calling
    it as ``task.run(fake_self, ...)`` would shift positional args
    one slot. We use ``__func__`` to invoke the underlying
    function and pass our fake ``self`` explicitly.
    """
    fake_self = MagicMock()
    fake_self.name = task.name
    fake_self.request = MagicMock()
    fake_self.request.retries = (request_overrides or {}).get("retries", 0)
    fake_self.request.id = (request_overrides or {}).get("id", "task-test")
    fake_self.max_retries = (
        max_retries_override
        if max_retries_override is not None
        else 3
    )
    func = task.run.__func__ if hasattr(task.run, "__func__") else task.run
    return func(fake_self, *args, **kwargs)
