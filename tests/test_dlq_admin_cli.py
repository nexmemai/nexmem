"""P11-I5 DLQ admin CLI tests."""
from __future__ import annotations

import json
from typing import Dict, List

import pytest

from app.config import settings


pytestmark = [pytest.mark.unit]


# Reuse the fake-redis pattern from the celery_locks tests.
class _FakeRedis:
    def __init__(self) -> None:
        self.lists: Dict[str, List[str]] = {}
        self.kv: Dict[str, str] = {}

    def lpush(self, name, value):
        self.lists.setdefault(name, []).insert(0, value)
        return len(self.lists[name])

    def lrem(self, name, count, value):
        items = self.lists.get(name, [])
        n = 0
        new_items = []
        for it in items:
            if it == value and n < count:
                n += 1
                continue
            new_items.append(it)
        self.lists[name] = new_items
        return n

    def lrange(self, name, start, end):
        items = self.lists.get(name, [])
        if end < 0:
            end = len(items) + end
        return items[start : end + 1]

    def delete(self, name):
        return 1 if self.lists.pop(name, None) is not None else 0


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")
    from scripts import dlq_admin

    monkeypatch.setattr(dlq_admin, "_get_redis", lambda: fake)
    return fake


def _seed(fake_redis, payloads):
    for p in payloads:
        fake_redis.lpush(settings.dlq_redis_key, json.dumps(p))


class TestList:
    def test_list_empty(self, fake_redis, capsys):
        from scripts.dlq_admin import main

        rc = main(["list"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "DLQ empty" in out

    def test_list_shows_entries(self, fake_redis, capsys):
        _seed(
            fake_redis,
            [
                {"task_id": "t1", "user_id": "u1", "error_type": "X", "error": "boom"},
                {"task_id": "t2", "user_id": "u2", "error_type": "Y", "error": "kaboom"},
            ],
        )
        from scripts.dlq_admin import main

        rc = main(["list"])
        out = capsys.readouterr().out
        assert rc == 0
        # Newest first; we lpush'd in order so t2 is at index 0.
        assert "t2" in out
        assert "t1" in out
        assert "u2" in out

    def test_list_filters_by_user(self, fake_redis, capsys):
        _seed(
            fake_redis,
            [
                {"task_id": "a", "user_id": "u1"},
                {"task_id": "b", "user_id": "u2"},
                {"task_id": "c", "user_id": "u1"},
            ],
        )
        from scripts.dlq_admin import main

        main(["list", "--user", "u1"])
        out = capsys.readouterr().out
        assert "u1" in out
        assert "u2" not in out


class TestReplay:
    def test_replay_dry_run_does_not_drain(self, fake_redis, capsys):
        _seed(fake_redis, [{"task_id": "t", "user_id": "u1", "days_old": 1}])
        from scripts.dlq_admin import main

        rc = main(["replay", "--dry-run"])
        assert rc == 0
        # Entry still present.
        assert len(fake_redis.lists[settings.dlq_redis_key]) == 1
        out = capsys.readouterr().out
        assert "DRY-RUN" in out

    def test_replay_drains_and_enqueues(self, fake_redis, monkeypatch, capsys):
        _seed(
            fake_redis,
            [
                {"task_id": "t1", "user_id": "u1", "days_old": 1},
                {"task_id": "t2", "user_id": "u2", "days_old": 2},
            ],
        )
        # Patch the celery task so we don't touch a real broker.
        calls: List[tuple] = []

        class _FakeAsyncResult:
            id = "fake"

        def _fake_delay(user_id, days_old):
            calls.append((user_id, days_old))
            return _FakeAsyncResult()

        from app import tasks as _tasks

        monkeypatch.setattr(
            _tasks.consolidate_user_memory_task, "delay", _fake_delay
        )

        from scripts.dlq_admin import main

        rc = main(["replay"])
        assert rc == 0
        assert {c[0] for c in calls} == {"u1", "u2"}
        # Both entries removed.
        assert fake_redis.lists.get(settings.dlq_redis_key, []) == []
        out = capsys.readouterr().out
        assert "REPLAYED" in out
        assert "replayed=2" in out

    def test_replay_filters_by_user(self, fake_redis, monkeypatch):
        _seed(
            fake_redis,
            [
                {"task_id": "t1", "user_id": "u1", "days_old": 1},
                {"task_id": "t2", "user_id": "u2", "days_old": 2},
            ],
        )
        calls: List[tuple] = []

        class _FakeAsyncResult:
            id = "fake"

        def _fake_delay(user_id, days_old):
            calls.append((user_id, days_old))
            return _FakeAsyncResult()

        from app import tasks as _tasks

        monkeypatch.setattr(
            _tasks.consolidate_user_memory_task, "delay", _fake_delay
        )

        from scripts.dlq_admin import main

        main(["replay", "--user", "u1"])
        assert calls == [("u1", 1)]
        # u2 still in the list.
        remaining = fake_redis.lists.get(settings.dlq_redis_key, [])
        assert len(remaining) == 1
        assert "u2" in remaining[0]


class TestPurge:
    def test_purge_without_confirm_refuses(self, fake_redis):
        _seed(fake_redis, [{"task_id": "t", "user_id": "u1"}])
        from scripts.dlq_admin import main

        rc = main(["purge"])
        assert rc == 2
        assert fake_redis.lists.get(settings.dlq_redis_key)

    def test_purge_with_confirm_wipes(self, fake_redis, capsys):
        _seed(fake_redis, [{"task_id": "t", "user_id": "u1"}])
        from scripts.dlq_admin import main

        rc = main(["purge", "--confirm"])
        assert rc == 0
        assert settings.dlq_redis_key not in fake_redis.lists
