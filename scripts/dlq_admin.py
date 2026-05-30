#!/usr/bin/env python3
"""DLQ inspection + replay CLI (P11-I5).

After a consolidation task hits its retry budget the failed payload
lands in a Redis list (``settings.dlq_redis_key``). This script
gives operators three commands so they don't have to hand-craft
``redis-cli`` invocations during an incident:

    python scripts/dlq_admin.py list                # newest 50
    python scripts/dlq_admin.py list --limit 200    # newest 200
    python scripts/dlq_admin.py replay              # replay every entry
    python scripts/dlq_admin.py replay --user u-1   # filter by user_id
    python scripts/dlq_admin.py purge --confirm     # wipe (irreversible)

``replay`` re-enqueues each entry via the normal Celery API
(``consolidate_user_memory_task.delay``) AND removes the
re-enqueued entry from the Redis list, so a successful re-run does
not bounce around forever. Replay is not atomic: if the broker is
down between LPUSH (re-enqueue) and LREM (remove), the next replay
run picks up the entry again. That's the safe direction.

Run with the same env vars as the worker (``REDIS_URL`` minimally).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional


def _get_settings():
    """Import settings late so the CLI doesn't fail on
    ``--help`` when env is incomplete.
    """
    from app.config import settings

    return settings


def _get_redis():
    settings = _get_settings()
    if not settings.redis_url:
        print(
            "ERROR: REDIS_URL is not set. The DLQ lives in Redis; "
            "this CLI cannot run without it.",
            file=sys.stderr,
        )
        sys.exit(1)
    import redis

    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _list_entries(limit: int) -> List[Dict[str, Any]]:
    settings = _get_settings()
    client = _get_redis()
    raw = client.lrange(settings.dlq_redis_key, 0, max(0, limit - 1))
    items: List[Dict[str, Any]] = []
    for r in raw:
        try:
            items.append(json.loads(r))
        except Exception:
            items.append({"raw": r})
    return items


def cmd_list(limit: int, user: Optional[str]) -> int:
    items = _list_entries(limit)
    if user:
        items = [i for i in items if str(i.get("user_id")) == user]
    if not items:
        print("DLQ empty.")
        return 0
    for i, item in enumerate(items, 1):
        print(
            f"[{i:03d}] task_id={item.get('task_id', '?')} "
            f"user_id={item.get('user_id', '?')} "
            f"error_type={item.get('error_type', '?')} "
            f"dlq_at={item.get('dlq_at', '?')}"
        )
        if item.get("error"):
            print(f"        error: {item['error']}")
    return 0


def cmd_replay(user: Optional[str], dry_run: bool) -> int:
    settings = _get_settings()
    client = _get_redis()

    # Pull every entry as raw strings so we can LREM them by exact value.
    raw = client.lrange(settings.dlq_redis_key, 0, -1)
    if not raw:
        print("DLQ empty.")
        return 0

    # Lazy import so --help stays fast and dependency-free.
    from app.tasks import consolidate_user_memory_task

    replayed = 0
    skipped = 0
    for entry_raw in raw:
        try:
            entry = json.loads(entry_raw)
        except Exception:
            entry = {}

        if user and str(entry.get("user_id")) != user:
            skipped += 1
            continue

        if dry_run:
            print(
                f"DRY: would replay user_id={entry.get('user_id')} "
                f"task_id={entry.get('task_id')}"
            )
            continue

        try:
            consolidate_user_memory_task.delay(
                str(entry["user_id"]),
                int(entry.get("days_old", 1)),
            )
            client.lrem(settings.dlq_redis_key, 1, entry_raw)
            replayed += 1
            print(
                f"REPLAYED user_id={entry.get('user_id')} "
                f"task_id={entry.get('task_id')}"
            )
        except Exception as exc:
            skipped += 1
            print(
                f"SKIPPED user_id={entry.get('user_id')} "
                f"reason={exc}"
            )

    summary = "DRY-RUN" if dry_run else "DONE"
    print(f"{summary}: replayed={replayed} skipped={skipped}")
    return 0


def cmd_purge(confirm: bool) -> int:
    if not confirm:
        print(
            "Refusing to purge without --confirm. This is irreversible.",
            file=sys.stderr,
        )
        return 2
    settings = _get_settings()
    client = _get_redis()
    n = client.delete(settings.dlq_redis_key)
    print(f"Purged {n} key(s).")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dlq_admin",
        description="Inspect, replay, or purge the consolidation DLQ.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="List DLQ entries (newest first).")
    pl.add_argument("--limit", type=int, default=50)
    pl.add_argument("--user", default=None)

    pr = sub.add_parser("replay", help="Re-enqueue every DLQ entry.")
    pr.add_argument("--user", default=None)
    pr.add_argument("--dry-run", action="store_true")

    pp = sub.add_parser("purge", help="DELETE every DLQ entry (irreversible).")
    pp.add_argument("--confirm", action="store_true")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args.limit, args.user)
    if args.cmd == "replay":
        return cmd_replay(args.user, args.dry_run)
    if args.cmd == "purge":
        return cmd_purge(args.confirm)
    return 1


if __name__ == "__main__":
    sys.exit(main())
