"""P3-A7 account-lockout escalation tests."""
from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.core import brute_force


pytestmark = [pytest.mark.unit]


class _FakeRequest:
    def __init__(self, ip: str) -> None:
        self.headers = {"X-Forwarded-For": ip}
        self.client = type("C", (), {"host": ip})


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    brute_force._store.clear()
    brute_force._reset_account_escalation()
    yield
    brute_force._store.clear()
    brute_force._reset_account_escalation()


@pytest.mark.asyncio
class TestEscalation:
    async def test_no_lock_within_single_ip(self, monkeypatch):
        """Failures from a single IP must not trigger account-level
        escalation — that's the per-IP brute-force tracker's job."""
        monkeypatch.setattr(settings, "account_lockout_escalation_threshold", 5)
        monkeypatch.setattr(settings, "account_lockout_escalation_window_seconds", 600)
        email = "victim@example.com"
        for _ in range(20):
            await brute_force.record_failure(_FakeRequest("10.0.0.1"), email)
        # All from one IP -> escalation must NOT fire.
        assert brute_force._account_is_locked(email) is False

    async def test_threshold_across_ips_locks_account(self, monkeypatch):
        monkeypatch.setattr(settings, "account_lockout_escalation_threshold", 5)
        monkeypatch.setattr(
            settings, "account_lockout_escalation_window_seconds", 600
        )
        monkeypatch.setattr(
            settings, "account_lockout_escalation_seconds", 3600
        )
        email = f"victim_{uuid.uuid4().hex[:6]}@example.com"
        # 5 failures across 5 distinct IPs -> escalate.
        for i in range(5):
            await brute_force.record_failure(_FakeRequest(f"10.0.0.{i+1}"), email)
        assert brute_force._account_is_locked(email) is True

    async def test_check_not_locked_raises_429(self, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setattr(settings, "account_lockout_escalation_threshold", 3)
        monkeypatch.setattr(
            settings, "account_lockout_escalation_window_seconds", 600
        )
        email = f"lockout_{uuid.uuid4().hex[:6]}@example.com"
        for i in range(3):
            await brute_force.record_failure(_FakeRequest(f"10.0.{i}.1"), email)
        with pytest.raises(HTTPException) as exc:
            await brute_force.check_not_locked(_FakeRequest("10.0.99.1"), email)
        assert exc.value.status_code == 429
        assert "support" in exc.value.detail.lower()

    async def test_lock_expires_after_window(self, monkeypatch):
        monkeypatch.setattr(settings, "account_lockout_escalation_threshold", 2)
        monkeypatch.setattr(
            settings, "account_lockout_escalation_window_seconds", 600
        )
        # 0-second lockout -> expires immediately for the test.
        monkeypatch.setattr(
            settings, "account_lockout_escalation_seconds", 0
        )
        email = f"exp_{uuid.uuid4().hex[:6]}@example.com"
        await brute_force.record_failure(_FakeRequest("1.1.1.1"), email)
        await brute_force.record_failure(_FakeRequest("2.2.2.2"), email)
        # With 0-second TTL the lock entry is expired on next check.
        import time

        time.sleep(0.001)
        assert brute_force._account_is_locked(email) is False
