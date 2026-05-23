"""nexmem-admin CLI — operator tooling (P11-I1, Block 6).

Five commands for ops work that does not fit cleanly into HTTP
routes (or where shell access is the right authn surface):

    rotate-secret-key       print a fresh JWT signing key for env rotation
    list-users [--limit N]  dump user roster (PII masked)
    force-revoke-key        hard-disable a single api_keys row
    show-user --user-id U   detail panel for one user (PII masked)
    show-queue-depth        Redis Celery queue depths

Auth posture
------------
The CLI assumes anyone who can run it is already authorised — they
have shell on the box that holds DATABASE_URL / REDIS_URL. There is
no in-CLI authentication step. The ``force-revoke-key`` command
still records an audit row (``actor_user_id`` = the affected user,
with ``actor="admin"`` in the JSONB payload) so an after-the-fact
review can attribute admin actions to whoever was running the
shell session.

Mode posture
------------
* ``DEMO_MODE=true`` (the project default) → every command operates
  on the in-memory ``app.demo_db`` store. No DB connection is opened.
* ``DEMO_MODE=false`` → the command opens a **synchronous**
  SQLAlchemy session on ``DATABASE_URL``. The CLI is not running
  inside an event loop, so we deliberately do not reuse the app's
  async engine. Each command is its own short-lived transaction so
  a CLI invocation cannot leave the connection pool in a half-state.

Sensitive fields never printed
------------------------------
``hashed_password``, ``totp_secret``, raw API keys, and the
unmasked email always stay out of stdout. ``list-users`` /
``show-user`` mask the email as ``a***@domain.com`` so an operator
can still distinguish two users at a glance without leaking the
full address into shell history or scrollback.
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import uuid
from datetime import datetime
from typing import Optional


logger = logging.getLogger("nexmem-admin")


# ── Display helpers ──────────────────────────────────────────────────────────
def _mask_email(email: Optional[str]) -> str:
    """Return ``a***@domain.com`` for display.

    Empty / ``None`` becomes ``<no-email>``. A missing ``@`` becomes
    ``<masked>`` rather than risking a leak.
    """
    if not email:
        return "<no-email>"
    if "@" not in email:
        return "<masked>"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _is_demo_mode() -> bool:
    """Re-import the live ``settings`` each call.

    Tests flip ``DEMO_MODE`` between subcommand invocations on the
    same process; reading the cached module-level ``settings`` would
    miss the toggle. Importing here keeps the helper honest.
    """
    from app.config import settings

    return bool(settings.demo_mode)


# ── Command 1: rotate-secret-key ─────────────────────────────────────────────
def cmd_rotate_secret_key(args: argparse.Namespace) -> int:
    """Print a fresh 64-char hex SECRET_KEY.

    Does NOT mutate any environment or config. Auto-rotating the
    live process would race in-flight requests against the new key
    and corrupt the JWT-signing surface — the operator must copy
    the value into the deploy environment and trigger a fresh
    deploy themselves.
    """
    new_key = secrets.token_hex(32)
    print(f"NEW_SECRET_KEY={new_key}")
    print()
    print("Next steps:")
    print(" 1. Copy the value above into your deploy env (Render / k8s / etc.)")
    print("    as the new SECRET_KEY.")
    print(" 2. Save and trigger a redeploy.")
    print(" 3. WARNING: This will invalidate every existing JWT (access AND")
    print("    refresh). Every user will be forced to re-login.")
    print(" 4. Auto-rotation is intentionally NOT performed here — the live")
    print("    process must restart against the new key to avoid signing")
    print("    half a request with the old key and verifying with the new.")
    return 0


# ── Command 2: list-users ────────────────────────────────────────────────────
def cmd_list_users(args: argparse.Namespace) -> int:
    limit = max(1, int(args.limit))
    if _is_demo_mode():
        return _list_users_demo(limit)
    return _list_users_db(limit)


def _list_users_demo(limit: int) -> int:
    from app import demo_db

    rows = list(demo_db.demo_users.values())[:limit]
    if not rows:
        print("(no users)")
        return 0
    _print_user_header()
    for r in rows:
        uid = str(r.get("id"))
        email = _mask_email(r.get("email"))
        active = "Y" if r.get("is_active") else "N"
        plan = r.get("tier", "free")
        # Demo mode tracks neither writes nor reads explicitly;
        # we surface the episodic-store length as a proxy for
        # "things this user has stored", labelled accordingly.
        write_count = len(demo_db.episodic_store.get(uid, []))
        created = r.get("created_at")
        created_s = created.isoformat() if created else ""
        print(
            f"{uid:<38} {email:<30} {active:<7} {plan:<10} "
            f"{write_count:<8} {created_s}"
        )
    return 0


def _list_users_db(limit: int) -> int:
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.config import settings
    from app.models.memory import EpisodicMemory
    from app.models.user import User

    sync_url = _sync_database_url(settings.effective_database_url)
    engine = create_engine(sync_url, future=True)
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(User).order_by(User.created_at.desc()).limit(limit)
            ).scalars().all()
            if not rows:
                print("(no users)")
                return 0
            _print_user_header()
            for u in rows:
                writes = session.execute(
                    select(func.count(EpisodicMemory.id)).where(
                        EpisodicMemory.user_id == u.id
                    )
                ).scalar() or 0
                print(
                    f"{str(u.id):<38} {_mask_email(u.email):<30} "
                    f"{'Y' if u.is_active else 'N':<7} {u.tier:<10} "
                    f"{writes:<8} "
                    f"{u.created_at.isoformat() if u.created_at else ''}"
                )
    finally:
        engine.dispose()
    return 0


def _print_user_header() -> None:
    print(
        f"{'user_id':<38} {'email':<30} {'active':<7} "
        f"{'plan':<10} {'writes':<8} created_at"
    )


# ── Command 3: force-revoke-key ──────────────────────────────────────────────
def cmd_force_revoke_key(args: argparse.Namespace) -> int:
    try:
        key_uuid = uuid.UUID(args.key_id)
    except ValueError:
        print(f"error: invalid key-id (must be a UUID): {args.key_id}", file=sys.stderr)
        return 2

    if _is_demo_mode():
        return _force_revoke_demo(key_uuid)
    return _force_revoke_db(key_uuid)


def _force_revoke_demo(key_uuid: uuid.UUID) -> int:
    from app import demo_db
    from app.core.audit_log import _demo_auth_log  # type: ignore[attr-defined]

    rec = demo_db.demo_api_keys.get(str(key_uuid))
    if rec is None:
        print(f"error: api key {key_uuid} not found", file=sys.stderr)
        return 1
    rec["is_active"] = False
    revoked_at = datetime.utcnow().isoformat() + "Z"
    # Mirror the prod audit path so test_force_revoke_key_in_demo_mode
    # can confirm the row landed.
    _demo_auth_log.setdefault(str(rec["user_id"]), []).append({
        "id": str(uuid.uuid4()),
        "actor_user_id": str(rec["user_id"]),
        "target_user_id": str(rec["user_id"]),
        "action": "admin_force_revoke_api_key",
        "payload": {
            "api_key_id": str(key_uuid),
            "actor": "admin",
            "via": "nexmem-admin CLI",
        },
        "ip_address": None,
        "user_agent": "nexmem-admin/0.1",
        "request_id": None,
        "created_at": revoked_at,
    })
    print(f"Key {key_uuid} revoked at {revoked_at}")
    return 0


def _force_revoke_db(key_uuid: uuid.UUID) -> int:
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import Session

    from app.config import settings
    from app.models.audit_log import AuthAuditLog
    from app.models.user import APIKey

    sync_url = _sync_database_url(settings.effective_database_url)
    engine = create_engine(sync_url, future=True)
    revoked_at = datetime.utcnow()
    try:
        with Session(engine) as session, session.begin():
            row = session.execute(
                update(APIKey)
                .where(APIKey.id == key_uuid)
                .values(is_active=False)
                .returning(APIKey.user_id)
            ).scalar_one_or_none()
            if row is None:
                print(f"error: api key {key_uuid} not found", file=sys.stderr)
                return 1
            session.add(
                AuthAuditLog(
                    actor_user_id=row,
                    target_user_id=row,
                    action="admin_force_revoke_api_key",
                    payload={
                        "api_key_id": str(key_uuid),
                        "actor": "admin",
                        "via": "nexmem-admin CLI",
                    },
                    user_agent="nexmem-admin/0.1",
                )
            )
        print(f"Key {key_uuid} revoked at {revoked_at.isoformat()}Z")
    finally:
        engine.dispose()
    return 0


# ── Command 4: show-user ─────────────────────────────────────────────────────
def cmd_show_user(args: argparse.Namespace) -> int:
    try:
        user_uuid = uuid.UUID(args.user_id)
    except ValueError:
        print(f"error: invalid user-id (must be a UUID): {args.user_id}", file=sys.stderr)
        return 2

    if _is_demo_mode():
        return _show_user_demo(user_uuid)
    return _show_user_db(user_uuid)


def _show_user_demo(user_uuid: uuid.UUID) -> int:
    from app import demo_db

    uid = str(user_uuid)
    rec = demo_db.demo_users.get(uid)
    if rec is None:
        print(f"error: user {user_uuid} not found", file=sys.stderr)
        return 1

    api_keys_active = sum(
        1
        for k in demo_db.demo_api_keys.values()
        if str(k["user_id"]) == uid and k.get("is_active")
    )
    write_count = len(demo_db.episodic_store.get(uid, []))
    semantic_count = len(demo_db.semantic_store.get(uid, []))
    # Demo store does not distinguish "reads"; we report semantic-store
    # length as a useful proxy and label it. Operators in production
    # see the real reads count from token_usage in _show_user_db.
    _print_user_panel(
        user_id=uid,
        email=_mask_email(rec.get("email")),
        is_active=bool(rec.get("is_active")),
        created_at=rec.get("created_at"),
        plan=rec.get("tier", "free"),
        total_writes=write_count,
        total_reads=semantic_count,
        totp_enabled=bool(rec.get("totp_enabled")),
        deletion_scheduled_for=rec.get("deletion_scheduled_for"),
        active_api_key_count=api_keys_active,
    )
    return 0


def _show_user_db(user_uuid: uuid.UUID) -> int:
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.config import settings
    from app.models.memory import EpisodicMemory
    from app.models.user import APIKey, TokenUsage, User

    sync_url = _sync_database_url(settings.effective_database_url)
    engine = create_engine(sync_url, future=True)
    try:
        with Session(engine) as session:
            user = session.execute(
                select(User).where(User.id == user_uuid)
            ).scalar_one_or_none()
            if user is None:
                print(f"error: user {user_uuid} not found", file=sys.stderr)
                return 1
            writes = session.execute(
                select(func.count(EpisodicMemory.id)).where(
                    EpisodicMemory.user_id == user_uuid
                )
            ).scalar() or 0
            reads = session.execute(
                select(func.count(TokenUsage.id)).where(
                    TokenUsage.user_id == user_uuid
                )
            ).scalar() or 0
            keys = session.execute(
                select(func.count(APIKey.id)).where(
                    APIKey.user_id == user_uuid,
                    APIKey.is_active.is_(True),
                )
            ).scalar() or 0
            _print_user_panel(
                user_id=str(user.id),
                email=_mask_email(user.email),
                is_active=user.is_active,
                created_at=user.created_at,
                plan=user.tier,
                total_writes=writes,
                total_reads=reads,
                totp_enabled=bool(user.totp_enabled),
                deletion_scheduled_for=user.deletion_scheduled_for,
                active_api_key_count=keys,
            )
    finally:
        engine.dispose()
    return 0


def _print_user_panel(
    *,
    user_id: str,
    email: str,
    is_active: bool,
    created_at,
    plan: str,
    total_writes: int,
    total_reads: int,
    totp_enabled: bool,
    deletion_scheduled_for,
    active_api_key_count: int,
) -> None:
    print(f"user_id:                  {user_id}")
    print(f"email (masked):           {email}")
    print(f"is_active:                {is_active}")
    print(
        "created_at:               "
        f"{created_at.isoformat() if created_at else ''}"
    )
    print(f"plan:                     {plan}")
    print(f"total_writes:             {total_writes}")
    print(f"total_reads:              {total_reads}")
    print(f"totp_enabled:             {totp_enabled}")
    if deletion_scheduled_for:
        sched = (
            deletion_scheduled_for.isoformat()
            if hasattr(deletion_scheduled_for, "isoformat")
            else str(deletion_scheduled_for)
        )
        print(f"deletion_scheduled_for:   {sched}")
    print(f"active_api_key_count:     {active_api_key_count}")


# ── Command 5: show-queue-depth ──────────────────────────────────────────────
def cmd_show_queue_depth(args: argparse.Namespace) -> int:
    """Print Celery queue depths from Redis.

    Reads the standard Celery queue keys (``celery``, ``high``,
    ``low``) plus the project's DLQ list. Falls back to ``celery``
    only if no other queues are configured. On Redis-unavailable we
    exit with code 1 — the CLI is intentionally noisier here than
    R-301 fail-open behaviour, because an operator running this
    command WANTS to know Redis is down.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        # Also accept settings.redis_url so the CLI honours .env files.
        from app.config import settings

        redis_url = settings.redis_url
    if not redis_url:
        print("error: REDIS_URL is not set", file=sys.stderr)
        return 1
    try:
        import redis  # noqa: WPS433
    except ImportError:
        print("error: redis package is not installed", file=sys.stderr)
        return 1

    try:
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
        client.ping()
    except Exception as exc:
        print(f"error: cannot reach Redis at {redis_url}: {exc}", file=sys.stderr)
        return 1

    from app.config import settings

    queue_names = ["celery", "high", "low", settings.dlq_redis_key]
    print(f"{'queue':<40} depth")
    for q in queue_names:
        try:
            depth = client.llen(q)
        except Exception as exc:
            print(f"{q:<40} <error: {exc}>")
            continue
        print(f"{q:<40} {depth}")
    return 0


# ── Internal helpers ─────────────────────────────────────────────────────────
def _sync_database_url(async_url: str) -> str:
    """Convert the app's async URL to a sync driver URL.

    ``postgresql+asyncpg://...`` → ``postgresql+psycopg2://...``.
    Other shapes are returned as-is so an operator with an exotic
    DSN can override and proceed.
    """
    if "+asyncpg" in async_url:
        return async_url.replace("+asyncpg", "+psycopg2")
    return async_url


# ── Argument parsing ─────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexmem-admin",
        description="Operator tooling for Nexmem (P11-I1).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "rotate-secret-key",
        help="Print a fresh 64-char hex SECRET_KEY for env rotation.",
    )

    p_list = sub.add_parser("list-users", help="List users (PII masked).")
    p_list.add_argument("--limit", type=int, default=50)

    p_rev = sub.add_parser(
        "force-revoke-key",
        help="Hard-disable a single api_keys row.",
    )
    p_rev.add_argument("--key-id", required=True, dest="key_id")

    p_show = sub.add_parser(
        "show-user", help="Detail panel for one user (PII masked)."
    )
    p_show.add_argument("--user-id", required=True, dest="user_id")

    sub.add_parser(
        "show-queue-depth", help="Print Celery queue depths from Redis."
    )
    return parser


_DISPATCH = {
    "rotate-secret-key": cmd_rotate_secret_key,
    "list-users": cmd_list_users,
    "force-revoke-key": cmd_force_revoke_key,
    "show-user": cmd_show_user,
    "show-queue-depth": cmd_show_queue_depth,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _DISPATCH.get(args.command)
    if handler is None:  # pragma: no cover - argparse should reject this
        parser.error(f"unknown command: {args.command}")
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
