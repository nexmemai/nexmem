"""Tests for per-user monthly token quota enforcement."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response

from app.core import usage_quota
from app.models.user import User


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value

    def scalar_one_or_none(self):
        return None


class FakeDB:
    def __init__(self, scalar_value=0):
        self.scalar_value = scalar_value
        self.statements = []
        self.added = []
        self.committed = False
        self.flushed = False
        self.rolled_back = False

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return FakeScalarResult(self.scalar_value)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def make_user(tier="free"):
    user = User(id=uuid.uuid4(), tier=tier, is_active=True)
    user.app_id = None
    return user


@pytest.mark.asyncio
async def test_under_quota_returns_remaining_usage(monkeypatch):
    monkeypatch.setattr(usage_quota.settings, "free_monthly_writes", 100)
    user = make_user("free")
    db = FakeDB(scalar_value=40)

    status = await usage_quota.enforce_usage_quota(db, user)

    assert status["tier"] == "free"
    assert status["quota"] == 100
    assert status["used"] == 40
    assert status["remaining"] == 60
    assert db.statements


@pytest.mark.asyncio
async def test_estimated_usage_within_quota_returns_remaining_after_estimate(monkeypatch):
    monkeypatch.setattr(usage_quota.settings, "free_monthly_writes", 100)
    user = make_user("free")
    db = FakeDB(scalar_value=40)

    status = await usage_quota.enforce_usage_quota(
        db,
        user,
        estimated_tokens=50,
    )

    assert status["remaining"] == 60
    assert status["estimated_tokens"] == 50
    assert status["remaining_after_estimate"] == 10
    headers = usage_quota.quota_headers(status)
    assert headers["X-RateLimit-Estimated-Cost"] == "50"
    assert headers["X-RateLimit-Remaining-After-Estimate"] == "10"


@pytest.mark.asyncio
async def test_estimated_usage_over_quota_raises_429(monkeypatch):
    monkeypatch.setattr(usage_quota.settings, "free_monthly_writes", 100)
    user = make_user("free")
    db = FakeDB(scalar_value=40)

    with pytest.raises(HTTPException) as exc:
        await usage_quota.enforce_usage_quota(
            db,
            user,
            estimated_tokens=80,
        )

    assert exc.value.status_code == 429
    assert exc.value.detail["error"] == "Estimated request would exceed monthly usage quota"
    assert exc.value.detail["remaining"] == 60
    assert exc.value.detail["estimated_tokens"] == 80
    assert exc.value.headers["X-RateLimit-Estimated-Cost"] == "80"


@pytest.mark.asyncio
async def test_quota_enforcement_acquires_user_transaction_lock(monkeypatch):
    monkeypatch.setattr(usage_quota.settings, "free_monthly_writes", 100)
    user = make_user("free")
    db = FakeDB(scalar_value=0)

    await usage_quota.enforce_usage_quota(
        db,
        user,
        estimated_tokens=10,
        acquire_lock=True,
    )

    assert "pg_advisory_xact_lock" in str(db.statements[0])
    assert len(db.statements) >= 2


@pytest.mark.asyncio
async def test_over_quota_raises_429_with_headers(monkeypatch):
    monkeypatch.setattr(usage_quota.settings, "free_monthly_writes", 100)
    user = make_user("free")
    db = FakeDB(scalar_value=100)

    with pytest.raises(HTTPException) as exc:
        await usage_quota.enforce_usage_quota(db, user)

    assert exc.value.status_code == 429
    assert exc.value.detail["remaining"] == 0
    assert exc.value.headers["X-RateLimit-Limit"] == "100"
    assert exc.value.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_unknown_tier_falls_back_to_free_quota(monkeypatch):
    monkeypatch.setattr(usage_quota.settings, "free_monthly_writes", 25)
    user = make_user("unexpected")
    db = FakeDB(scalar_value=5)

    status = await usage_quota.enforce_usage_quota(db, user)

    assert status["tier"] == "unexpected"
    assert status["quota"] == 25
    assert status["remaining"] == 20


@pytest.mark.asyncio
async def test_rag_rejects_over_quota_before_expensive_work(monkeypatch):
    from app.routers import rag
    from app.schemas.memory import RAGRequest

    user = make_user("free")
    request = RAGRequest(user_id=str(user.id), message="Tell me everything")
    expensive_retrieval = AsyncMock()
    monkeypatch.setattr(rag.settings, "demo_mode", False)
    monkeypatch.setattr(rag, "get_retrieval_context", expensive_retrieval)

    async def reject_quota(db, current_user, **kwargs):
        raise HTTPException(status_code=429, detail={"error": "quota"})

    monkeypatch.setattr(rag, "enforce_usage_quota", reject_quota)

    with pytest.raises(HTTPException) as exc:
        await rag.rag_chat(request, Response(), current_user=user, db=FakeDB())

    assert exc.value.status_code == 429
    expensive_retrieval.assert_not_called()


@pytest.mark.asyncio
async def test_rag_rejects_over_estimated_request_before_llm(monkeypatch):
    from app.routers import rag
    from app.schemas.memory import RAGRequest
    import app.services.llm as llm_module

    monkeypatch.setattr(usage_quota.settings, "free_monthly_writes", 100)
    user = make_user("free")
    request = RAGRequest(
        user_id=str(user.id),
        message="Summarize all context",
        include_procedural=False,
    )
    db = FakeDB(scalar_value=40)
    llm_call = MagicMock(return_value={"reply": "should not run"})

    monkeypatch.setattr(rag.settings, "demo_mode", False)
    monkeypatch.setattr(rag, "estimate_rag_request_tokens", MagicMock(side_effect=[10, 80]))
    monkeypatch.setattr(
        rag,
        "get_retrieval_context",
        AsyncMock(
            return_value={
                "episodic_context": ["large memory context"],
                "semantic_context": [],
                "graph_context": [],
            }
        ),
    )
    monkeypatch.setattr(llm_module.llm_service, "generate_rag_response", llm_call)

    with pytest.raises(HTTPException) as exc:
        await rag.rag_chat(request, Response(), current_user=user, db=db)

    assert exc.value.status_code == 429
    assert exc.value.detail["estimated_tokens"] == 80
    llm_call.assert_not_called()


@pytest.mark.asyncio
async def test_rag_under_quota_sets_headers_and_tracks_usage(monkeypatch):
    from app.routers import rag
    from app.schemas.memory import RAGRequest
    import app.services.llm as llm_module

    user = make_user("starter")
    request = RAGRequest(
        user_id=str(user.id),
        message="Hello",
        include_procedural=False,
        app_id="app-test",
    )
    response = Response()
    db = FakeDB()
    llm_call = MagicMock(
        return_value={
            "reply": "OK",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "latency_ms": 1.0,
        }
    )

    monkeypatch.setattr(rag.settings, "demo_mode", False)
    monkeypatch.setattr(
        rag,
        "enforce_usage_quota",
        AsyncMock(
            return_value={
                "tier": "starter",
                "quota": 10000,
                "used": 100,
                "remaining": 9900,
                "reset_at": "2026-06-01T00:00:00+00:00",
            }
        ),
    )
    monkeypatch.setattr(
        rag,
        "get_retrieval_context",
        AsyncMock(
            return_value={
                "episodic_context": [],
                "semantic_context": [],
                "graph_context": [],
            }
        ),
    )
    monkeypatch.setattr(llm_module.llm_service, "generate_rag_response", llm_call)

    result = await rag.rag_chat(request, response, current_user=user, db=db)

    assert result["reply"] == "OK"
    assert response.headers["X-RateLimit-Limit"] == "10000"
    assert response.headers["X-RateLimit-Remaining"] == "9900"
    assert db.flushed is True
    assert db.committed is True
    usage = next(item for item in db.added if item.__class__.__name__ == "TokenUsage")
    assert str(usage.user_id) == str(user.id)
    assert usage.app_id == "app-test"
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5
    assert usage.total_tokens == 15
    _, kwargs = llm_call.call_args
    assert "user_id" not in kwargs
    assert "app_id" not in kwargs


@pytest.mark.asyncio
async def test_rag_tracking_failure_keeps_successful_llm_reply(monkeypatch):
    from app.routers import rag
    from app.schemas.memory import RAGRequest
    import app.services.llm as llm_module

    user = make_user("starter")
    request = RAGRequest(
        user_id=str(user.id),
        message="Hello",
        include_procedural=False,
        app_id="app-test",
    )
    response = Response()
    db = FakeDB()
    llm_call = MagicMock(
        return_value={
            "reply": "real model reply",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "latency_ms": 1.0,
        }
    )
    tracking_error = RuntimeError("usage flush failed")
    logger_error = MagicMock()

    monkeypatch.setattr(rag.settings, "demo_mode", False)
    monkeypatch.setattr(
        rag,
        "enforce_usage_quota",
        AsyncMock(
            return_value={
                "tier": "starter",
                "quota": 10000,
                "used": 100,
                "remaining": 9900,
                "reset_at": "2026-06-01T00:00:00+00:00",
            }
        ),
    )
    monkeypatch.setattr(
        rag,
        "get_retrieval_context",
        AsyncMock(
            return_value={
                "episodic_context": [],
                "semantic_context": [],
                "graph_context": [],
            }
        ),
    )
    monkeypatch.setattr(llm_module.llm_service, "generate_rag_response", llm_call)
    monkeypatch.setattr(
        llm_module,
        "track_token_usage",
        AsyncMock(side_effect=tracking_error),
    )
    monkeypatch.setattr(rag.logger, "error", logger_error)

    result = await rag.rag_chat(request, response, current_user=user, db=db)

    assert result["reply"] == "real model reply"
    assert response.headers["X-Token-Usage-Tracking"] == "failed"
    assert db.rolled_back is True
    assert db.committed is True
    logger_error.assert_any_call(
        "token_usage_tracking_failed",
        error="usage flush failed",
        user_id=str(user.id),
        app_id="app-test",
        prompt_tokens=10,
        completion_tokens=5,
        exc_info=True,
    )


@pytest.mark.asyncio
async def test_rag_prompt_budget_error_returns_413(monkeypatch):
    from app.routers import rag
    from app.schemas.memory import RAGRequest
    import app.services.llm as llm_module

    user = make_user("starter")
    request = RAGRequest(
        user_id=str(user.id),
        message="too large",
        include_procedural=False,
    )
    db = FakeDB()
    llm_call = MagicMock(
        side_effect=llm_module.PromptBudgetExceededError(
            "Request is too large for the model context window. Shorten the message and try again."
        )
    )

    monkeypatch.setattr(rag.settings, "demo_mode", False)
    monkeypatch.setattr(
        rag,
        "enforce_usage_quota",
        AsyncMock(
            return_value={
                "tier": "starter",
                "quota": 10000,
                "used": 100,
                "remaining": 9900,
                "reset_at": "2026-06-01T00:00:00+00:00",
            }
        ),
    )
    monkeypatch.setattr(
        rag,
        "get_retrieval_context",
        AsyncMock(
            return_value={
                "episodic_context": [],
                "semantic_context": [],
                "graph_context": [],
            }
        ),
    )
    monkeypatch.setattr(llm_module.llm_service, "generate_rag_response", llm_call)

    with pytest.raises(HTTPException) as exc:
        await rag.rag_chat(request, Response(), current_user=user, db=db)

    assert exc.value.status_code == 413
    assert "Request is too large" in exc.value.detail
    assert db.added == []
    assert db.committed is False


@pytest.mark.asyncio
async def test_usage_endpoint_returns_quota_status(monkeypatch):
    from app.routers import auth

    expected = {
        "tier": "free",
        "quota": 1000,
        "used": 10,
        "remaining": 990,
        "reset_at": "2026-06-01T00:00:00+00:00",
    }
    monkeypatch.setattr(auth.settings, "demo_mode", False)
    monkeypatch.setattr(auth, "get_usage_quota_status", AsyncMock(return_value=expected))
    set_rls_context = AsyncMock()
    monkeypatch.setattr(auth, "set_rls_context", set_rls_context)

    class FakeSessionManager:
        async def __aenter__(self):
            return FakeDB()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(auth, "async_session", lambda: FakeSessionManager())

    result = await auth.get_current_user_usage(current_user=make_user("free"))

    assert result == expected
    set_rls_context.assert_awaited_once()
