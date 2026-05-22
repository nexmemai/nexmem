"""Atomicity tests for `POST /api/v1/memory/episode/write`.

The risk we are guarding against (R-H1):
    The previous implementation wrapped the semantic-insert and engram-insert
    statements in `try/except` that swallowed exceptions. The outer `get_db`
    dependency commits at the end, so an episodic row would be persisted even
    if the semantic / engram / graph writes failed. Result: orphan rows.

What we test here (without a live Postgres):
    1. **Source contract** — the production write_episode() must NOT swallow
       DB errors with a try/except that logs and continues. Anything inside
       the transactional phase must propagate.
    2. **Pre-DB failure isolation** — when the embedder fails before any DB
       write, the route returns 502 and never opens a transaction.
    3. **Pre-DB failure isolation** — when engram processing fails before
       any DB write, the route returns 503 and never opens a transaction.
    4. **DB failure rollback** — when any DB statement after the first INSERT
       fails, the exception propagates so `get_db` rolls back. We simulate
       this with an AsyncSession stub that fails on the Nth `execute()` call.

A separate live-Postgres atomicity test lives under
`tests/integration/test_episode_write_atomicity_real_db.py` and is gated on
RUN_DB_TESTS=1 — it proves the rollback at the database layer.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parent.parent
MEMORY_ROUTER = REPO_ROOT / "app" / "routers" / "memory.py"


@pytest.fixture
def production_mode(monkeypatch):
    """Flip the loaded `settings.demo_mode` to False for these tests.

    The pytest.ini env block sets DEMO_MODE=true at session start, which
    is captured by `app.config.settings` at import. Mutating the env var
    after import has no effect; we must patch the loaded singleton.

    We patch the *router's* binding rather than `app.config.settings`
    directly, because earlier tests in the suite (e.g.
    test_app_startup_safety) reload `app.config`, which produces a new
    `settings` object in `sys.modules` but does NOT update the binding
    inside `app.routers.memory`. The route's view of settings is the
    only one that matters.
    """
    from app.routers import memory as memory_module

    monkeypatch.setattr(memory_module.settings, "demo_mode", False)
    yield


# ── 1. Source-level contract ────────────────────────────────────────────────


def test_write_episode_does_not_swallow_db_errors() -> None:
    """The production path of write_episode() must not contain the pattern
    `await db.execute(...)` followed by an `except Exception` clause that
    only `logger.warning(...)`s. That pattern is what produced R-H1.

    We allow `try/except` around the embedder and engram-processor pre-DB
    work (those raise HTTPException), but never around `db.execute(...)`.
    """
    src = MEMORY_ROUTER.read_text()
    # Restrict the search to the body of write_episode().
    start = src.index("async def write_episode(")
    end = src.index("\n@router", start)
    body = src[start:end]

    # The production path begins after the demo_mode return; everything below
    # 'Production path' is what we audit.
    prod_marker = "Production path"
    assert prod_marker in body, "expected 'Production path' marker in write_episode"
    prod_section = body[body.index(prod_marker):]

    # Find every try-block in the production section. Any try whose except
    # clause is followed only by a logger call (not raise) is suspect.
    swallow_pattern = re.compile(
        r"except[^:]*:\s*\n\s*logger\.(?:warning|info|debug|error)\([^)]*\)\s*\n\s*(?!raise)",
        flags=re.DOTALL,
    )
    matches = swallow_pattern.findall(prod_section)
    # There may be try/except blocks that re-raise an HTTPException; those
    # are not swallow patterns. Filter them.
    real_swallows = [m for m in matches if "raise " not in m]
    assert not real_swallows, (
        "write_episode production path contains exception-swallowing pattern(s) "
        "that re-introduce R-H1. Each try/except around DB work must propagate.\n"
        f"Offending blocks:\n{real_swallows}"
    )


# ── 2 & 3. Pre-DB failure isolation ─────────────────────────────────────────


def _make_request_body():
    from app.routers.memory import EpisodeWriteRequest

    return EpisodeWriteRequest(
        content="A test memory line.",
        session_id="atomicity-test",
        app_id=None,
        tags=[],
        metadata={},
    )


class _SpySession:
    """Tracks every db.execute() call made during the test."""

    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, stmt, params=None):
        self.executed.append(str(stmt))
        # Return a mock with the API the route expects.
        result = MagicMock()
        result.fetchone.return_value = (uuid4(),)
        return result

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_embedder_failure_returns_502_and_does_not_touch_db(
    production_mode, monkeypatch
) -> None:
    """If the embedder raises, the route must respond 502 and never query the DB."""
    from app.routers import memory as memory_module

    async def _raise(*_a, **_k):
        raise RuntimeError("embedder backend down")

    monkeypatch.setattr(memory_module.embedder, "embed", _raise)

    spy_db = _SpySession()
    body = _make_request_body()
    user = MagicMock(id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await memory_module.write_episode(body=body, current_user=user, db=spy_db)

    assert exc.value.status_code == 502
    assert spy_db.executed == [], (
        "DB was queried before the embedder result was confirmed: "
        f"{spy_db.executed}"
    )


@pytest.mark.asyncio
async def test_engram_failure_returns_503_and_does_not_touch_db(
    production_mode, monkeypatch
) -> None:
    """If the engram processor raises, the route must respond 503 and skip DB."""
    from app.routers import memory as memory_module

    async def _ok_embed(*_a, **_k):
        return [0.0] * 384

    async def _engram_fail(*_a, **_k):
        raise RuntimeError("spaCy not loaded")

    monkeypatch.setattr(memory_module.embedder, "embed", _ok_embed)
    monkeypatch.setattr(memory_module.engram_processor, "process_async", _engram_fail)

    spy_db = _SpySession()
    body = _make_request_body()
    user = MagicMock(id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await memory_module.write_episode(body=body, current_user=user, db=spy_db)

    assert exc.value.status_code == 503
    assert spy_db.executed == [], (
        "DB was queried before engram processing succeeded: "
        f"{spy_db.executed}"
    )


# ── 4. DB failure mid-write propagates (no swallow) ────────────────────────


class _FailOnNthSession:
    """Stub session that fails the Nth execute() call.

    fetchone() returns a fresh UUID on success.
    """

    def __init__(self, fail_at: int):
        self.fail_at = fail_at
        self.executed = 0
        self.flush_calls = 0

    async def execute(self, stmt, params=None):
        self.executed += 1
        if self.executed >= self.fail_at:
            raise RuntimeError(f"simulated DB failure at execute #{self.executed}")
        result = MagicMock()
        result.fetchone.return_value = (uuid4(),)
        result.scalar_one_or_none.return_value = None
        return result

    async def flush(self):
        self.flush_calls += 1
        return None

    def add(self, _obj):
        return None


@pytest.mark.asyncio
async def test_db_failure_during_semantic_insert_propagates(
    production_mode, monkeypatch
) -> None:
    """If the semantic INSERT (the second execute) fails, the exception must
    propagate. `get_db` (not under test here) is responsible for the actual
    rollback; the integration test under tests/integration verifies that.
    """
    from app.routers import memory as memory_module

    async def _ok_embed(*_a, **_k):
        return [0.0] * 384

    async def _ok_engram(*_a, **_k):
        return {
            "engram_id": "abc123",
            "distilled_text": "x",
            "dense_embedding": [],
            "actions": [],
            "objects": [],
            "entities": [],
            "negated_actions": [],
            "salience_scores": {},
            "connections": [],
            "original_length": 1,
            "compressed_length": 1,
            "compression_ratio": 0.0,
            "graph_edges": [],
        }

    monkeypatch.setattr(memory_module.embedder, "embed", _ok_embed)
    monkeypatch.setattr(memory_module.engram_processor, "process_async", _ok_engram)

    db = _FailOnNthSession(fail_at=2)  # episodic OK, semantic fails
    body = _make_request_body()
    user = MagicMock(id=uuid4())

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await memory_module.write_episode(body=body, current_user=user, db=db)

    # The exception escaped — `get_db` will see it and rollback. The route
    # itself must NOT have caught the error and continued.
    assert db.executed == 2, "expected exactly 2 execute calls before propagation"


@pytest.mark.asyncio
async def test_db_failure_during_engram_insert_propagates(
    production_mode, monkeypatch
) -> None:
    """If the engram INSERT fails, the exception must propagate."""
    from app.routers import memory as memory_module

    async def _ok_embed(*_a, **_k):
        return [0.0] * 384

    async def _ok_engram(*_a, **_k):
        return {
            "engram_id": "abc123",
            "distilled_text": "x",
            "dense_embedding": [],
            "actions": [],
            "objects": [],
            "entities": [],
            "negated_actions": [],
            "salience_scores": {},
            "connections": [],
            "original_length": 1,
            "compressed_length": 1,
            "compression_ratio": 0.0,
            "graph_edges": [],
        }

    monkeypatch.setattr(memory_module.embedder, "embed", _ok_embed)
    monkeypatch.setattr(memory_module.engram_processor, "process_async", _ok_engram)

    db = _FailOnNthSession(fail_at=3)  # episodic OK, semantic OK, engram fails
    body = _make_request_body()
    user = MagicMock(id=uuid4())

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await memory_module.write_episode(body=body, current_user=user, db=db)

    assert db.executed == 3, "expected 3 execute calls before propagation"
