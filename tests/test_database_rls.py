"""Regression tests for async RLS context setup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import database


@pytest.mark.asyncio
async def test_set_rls_context_noops_without_user_id():
    session = SimpleNamespace(execute=AsyncMock())

    await database.set_rls_context(session, None)

    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_set_rls_context_uses_async_session_execute():
    session = SimpleNamespace(execute=AsyncMock())

    await database.set_rls_context(session, "user-123")

    session.execute.assert_awaited_once()
    statement, params = session.execute.await_args.args
    assert str(statement) == "SELECT set_config('app.current_user_id', :uid, true)"
    assert params == {"uid": "user-123"}


@pytest.mark.asyncio
async def test_set_auth_lookup_context_sets_only_provided_values():
    session = SimpleNamespace(execute=AsyncMock())

    await database.set_auth_lookup_context(
        session,
        email="user@example.com",
        api_key_hash="abc123",
    )

    assert session.execute.await_count == 2
    calls = session.execute.await_args_list
    assert str(calls[0].args[0]) == "SELECT set_config(:key, :value, true)"
    assert calls[0].args[1] == {
        "key": "app.auth_email",
        "value": "user@example.com",
    }
    assert calls[1].args[1] == {
        "key": "app.api_key_hash",
        "value": "abc123",
    }


class FakeSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()


class FakeSessionManager:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_get_db_sets_rls_context_from_request_state(monkeypatch):
    session = FakeSession()
    set_rls_context = AsyncMock()
    request = SimpleNamespace(state=SimpleNamespace(current_user_id="user-456"))

    monkeypatch.setattr(database, "async_session", lambda: FakeSessionManager(session))
    monkeypatch.setattr(database, "set_rls_context", set_rls_context)

    db_generator = database.get_db(request)
    yielded_session = await db_generator.__anext__()

    assert yielded_session is session
    set_rls_context.assert_awaited_once_with(session, "user-456")

    with pytest.raises(StopAsyncIteration):
        await db_generator.__anext__()
    session.commit.assert_awaited_once()
    session.rollback.assert_not_called()
    session.close.assert_awaited_once()


def test_sync_after_begin_listener_removed():
    assert not hasattr(database, "set_rls_context_on_begin")
