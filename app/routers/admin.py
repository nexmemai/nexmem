"""Admin endpoints (P11-I2/I3/I4, Block 6).

Three groups of routes, all gated by ``X-Admin-Key`` (see
``app.core.admin_auth``):

  P11-I3  POST /api/v1/admin/users/{user_id}/force-logout
          POST /api/v1/admin/users/{user_id}/impersonate    (Task 3)
  P11-I4  GET  /api/v1/admin/analytics/usage                 (Task 4)

Audit trail
-----------
Every admin action records to ``auth_audit_log`` with the JSONB
``payload`` carrying ``actor: "admin"`` and any action-specific
context. The schema's ``actor_user_id`` column is a non-NULL FK to
``users.id``, so the literal string ``"admin"`` cannot live there;
``record_auth_event`` falls back to the target user when
``actor_user_id`` is not a UUID, which is the desired behaviour —
the audit row is keyed on the affected user, with the admin marker
in the payload.

Force-logout posture
--------------------
Two things happen on a force-logout:

  1. Per-user access-token cutoff is set in Redis (production) or
     in ``demo_db.demo_force_logout`` (demo). Any access token
     whose ``iat`` claim is older than the cutoff is rejected by
     ``decode_token`` (production) or by the demo bearer branch
     in ``app.core.deps``.
  2. Every active refresh token for the user is revoked. The user
     cannot mint a fresh access token without re-authenticating
     via password / wallet.

After force-logout the user can still re-login. The new access
token's ``iat`` is greater than the cutoff, so it passes through.
This is intentional — force-logout is for "kill all current
sessions on this account NOW", not for "ban this account from
logging in", which is a separate (future) admin action.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import demo_auth
from app.core.admin_auth import get_admin_user
from app.core.audit_log import record_auth_event
from app.core.token_blocklist import revoke_user_tokens
from app.database import get_db
from app.models.apps import App
from app.models.auth import RefreshToken


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ── P11-I3: force-logout ─────────────────────────────────────────────────────
@router.post("/users/{user_id}/force-logout")
async def force_logout_user(
    user_id: str,
    request: Request,
    _admin: bool = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Terminate every active session for the target user.

    Returns ``{logged_out: True, user_id, sessions_terminated}``.
    ``sessions_terminated`` counts refresh tokens that were
    actively revoked by this call (not previously-revoked rows).
    """
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id (must be a UUID)",
        )

    sessions_terminated = 0

    if settings.demo_mode:
        # Set the cutoff timestamp *first*, then revoke refresh tokens.
        # If we revoked first and crashed before the cutoff, the user
        # could re-login during the same window without their old
        # access token being killed.
        from app import demo_db

        cutoff = int(datetime.now(timezone.utc).timestamp())
        # Add 1 to the cutoff so a token with iat == now() is also
        # rejected. Without the +1 there is a 1-second window where
        # a token issued in the same wall-clock second could slip
        # through (iat == cutoff, the inequality is strict).
        demo_db.demo_force_logout[str(user_uuid)] = cutoff + 1
        # Count active refresh tokens BEFORE revoking so the response
        # reflects the actual session count we just killed.
        active = [
            rec
            for rec in demo_db.demo_refresh_tokens.values()
            if str(rec["user_id"]) == str(user_uuid)
            and rec.get("revoked_at") is None
            and rec.get("expires_at") > datetime.utcnow()
        ]
        sessions_terminated = len(active)
        demo_auth.revoke_all_refresh_tokens(user_uuid)
    else:
        # Production: count active refresh tokens, set Redis cutoff,
        # bulk-revoke. Same ordering: cutoff first.
        if not revoke_user_tokens(str(user_uuid)):
            # Redis unavailable — refuse to claim a logout we cannot
            # back up at the access-token level. The refresh-token
            # bulk revoke would still succeed, but the operator
            # invoking this route deserves a clear 503 rather than
            # a half-success.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Force-logout requires Redis (token blocklist). "
                    "Refresh tokens can still be revoked manually via the "
                    "database."
                ),
            )
        from sqlalchemy import select

        active_q = await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_uuid,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.utcnow(),
            )
        )
        sessions_terminated = len(active_q.scalars().all())
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_uuid,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.utcnow())
        )
        await db.commit()

    await record_auth_event(
        "admin_force_logout",
        target_user_id=user_uuid,
        request=request,
        payload={
            "actor": "admin",
            "sessions_terminated": sessions_terminated,
        },
    )

    logger.info(
        "admin.force_logout: user_id=%s sessions_terminated=%d",
        user_uuid,
        sessions_terminated,
    )
    return {
        "logged_out": True,
        "user_id": str(user_uuid),
        "sessions_terminated": sessions_terminated,
    }




# ── P11-I2: support impersonation ────────────────────────────────────────────
@router.post("/users/{user_id}/impersonate")
async def impersonate_user(
    user_id: str,
    request: Request,
    _admin: bool = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Mint a 1-hour JWT that lets the admin act as ``user_id``.

    Returns:

      ``impersonation_token``  short-lived JWT, type=impersonation
      ``expires_in``           seconds until the token expires (3600)
      ``warning``              text the admin UI MUST surface to the
                               operator before they reuse the token

    The audit trail is two-layered:

      1. ``admin_impersonation_started`` is recorded once here.
      2. Every subsequent request bearing this token records an
         ``impersonation_request`` row in ``get_current_user``
         (see ``app.core.deps``). That request-level granularity
         is the whole point — an after-the-fact review must be
         able to answer "what did the admin do while impersonating
         user X?", not just "did the admin start a session?".

    The target user must exist; we do NOT silently mint tokens for
    unknown ids. Inactive users (e.g. soft-delete grace period)
    can still be impersonated — admin investigation often needs
    exactly that case.
    """
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id (must be a UUID)",
        )

    # Verify the user exists. Mint-then-fail would be confusing.
    if settings.demo_mode:
        target = demo_auth.get_user_by_id(str(user_uuid))
        target_exists = target is not None
    else:
        from sqlalchemy import select

        from app.models.user import User

        result = await db.execute(select(User.id).where(User.id == user_uuid))
        target_exists = result.scalar_one_or_none() is not None

    if not target_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found",
        )

    from app.core.security import (
        IMPERSONATION_TOKEN_TTL_SECONDS,
        create_impersonation_token,
    )

    token = create_impersonation_token(str(user_uuid))

    await record_auth_event(
        "admin_impersonation_started",
        target_user_id=user_uuid,
        request=request,
        payload={
            "actor": "admin",
            "expires_in": IMPERSONATION_TOKEN_TTL_SECONDS,
        },
    )

    logger.info(
        "admin.impersonate: target=%s ttl=%ss",
        user_uuid,
        IMPERSONATION_TOKEN_TTL_SECONDS,
    )

    return {
        "impersonation_token": token,
        "expires_in": IMPERSONATION_TOKEN_TTL_SECONDS,
        "warning": (
            "This token is logged. All actions are attributed to the "
            "target user but the audit log preserves actor=admin."
        ),
    }




# ── P11-I4: usage analytics ──────────────────────────────────────────────────
def _celery_queue_depth() -> int | str:
    """Best-effort Celery queue depth.

    Returns the integer depth on success, or the string ``"unavailable"``
    when Redis is unreachable. The fail-open string is intentional —
    R-301 already documents Redis fail-open as accepted-for-private-beta
    behaviour, so an analytics endpoint that 500s on a Redis blip would
    be a regression of that posture. Operators can still tell something
    is wrong (the value is a string, not an int) without losing the
    rest of the analytics payload.
    """
    if not settings.redis_url:
        return "unavailable"
    try:
        import redis  # noqa: WPS433

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        # Sum the standard Celery queue + the project's DLQ. Other queues
        # (high / low) are optional; an llen on a missing key returns 0,
        # which is the right answer.
        depth = 0
        for q in ("celery", "high", "low", settings.dlq_redis_key):
            try:
                depth += client.llen(q)
            except Exception:
                # A single broken queue should not nuke the whole metric.
                continue
        return int(depth)
    except Exception as exc:
        logger.warning(
            "admin.analytics: celery queue depth unavailable: %s", exc
        )
        return "unavailable"


def _demo_analytics_fixture() -> dict:
    """Plausible static values so the endpoint is testable without a DB.

    Numbers are intentionally non-zero so a UI rendering against the
    fixture sees realistic shapes (no division-by-zero in computed
    percentages, etc.).
    """
    return {
        "active_users_last_30d": 7,
        "total_writes_today": 42,
        "total_reads_today": 113,
        "total_writes_this_month": 1_205,
        "total_reads_this_month": 8_419,
        "top_apps_by_writes": [
            {"app_id": "demo-app-1", "write_count": 312},
            {"app_id": "demo-app-2", "write_count": 188},
        ],
        "users_by_plan": {"free": 5, "starter": 1, "pro": 1},
        "deletion_requests_pending": 0,
    }


@router.get("/analytics/usage")
async def usage_analytics(
    request: Request,
    _admin: bool = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Operator dashboard data — write/read totals, plan mix, queue depth.

    The shape is deliberately flat (no nested counters keyed on time
    buckets) so a thin UI can render it without joining or pivoting.
    Heavy aggregations (per-app top-N, per-day buckets) belong in a
    dedicated metrics pipeline; this endpoint covers the operator
    "is everything roughly OK?" use case.
    """
    queue_depth = _celery_queue_depth()
    generated_at = datetime.utcnow().isoformat() + "Z"

    if settings.demo_mode:
        body = _demo_analytics_fixture()
        body["generated_at"] = generated_at
        body["celery_queue_depth"] = queue_depth
        return body

    # Production aggregation. Each query is independent so a slow one
    # cannot block the others. The endpoint is admin-only and called
    # rarely (operator dashboard refreshes), so we accept the simple
    # one-trip-per-metric shape over batched analytics.
    from sqlalchemy import distinct, func, select

    from app.models.memory import EpisodicMemory
    from app.models.user import TokenUsage, User

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    thirty_days_ago = now - timedelta(days=30)

    active_30d = await db.execute(
        select(func.count(distinct(EpisodicMemory.user_id))).where(
            EpisodicMemory.created_at >= thirty_days_ago
        )
    )
    writes_today = await db.execute(
        select(func.count(EpisodicMemory.id)).where(
            EpisodicMemory.created_at >= today_start
        )
    )
    writes_month = await db.execute(
        select(func.count(EpisodicMemory.id)).where(
            EpisodicMemory.created_at >= month_start
        )
    )
    reads_today = await db.execute(
        select(func.count(TokenUsage.id)).where(
            TokenUsage.created_at >= today_start
        )
    )
    reads_month = await db.execute(
        select(func.count(TokenUsage.id)).where(
            TokenUsage.created_at >= month_start
        )
    )
    top_apps = await db.execute(
        select(
            EpisodicMemory.app_id,
            func.count(EpisodicMemory.id).label("write_count"),
        )
        .where(
            EpisodicMemory.app_id.is_not(None),
            EpisodicMemory.created_at >= month_start,
        )
        .group_by(EpisodicMemory.app_id)
        .order_by(func.count(EpisodicMemory.id).desc())
        .limit(10)
    )
    plan_rows = await db.execute(
        select(User.tier, func.count(User.id)).group_by(User.tier)
    )
    deletion_pending = await db.execute(
        select(func.count(User.id)).where(
            User.deletion_scheduled_for.is_not(None)
        )
    )

    return {
        "generated_at": generated_at,
        "active_users_last_30d": int(active_30d.scalar() or 0),
        "total_writes_today": int(writes_today.scalar() or 0),
        "total_reads_today": int(reads_today.scalar() or 0),
        "total_writes_this_month": int(writes_month.scalar() or 0),
        "total_reads_this_month": int(reads_month.scalar() or 0),
        "top_apps_by_writes": [
            {"app_id": str(row[0]), "write_count": int(row[1])}
            for row in top_apps.all()
        ],
        "celery_queue_depth": queue_depth,
        "users_by_plan": {tier: int(count) for tier, count in plan_rows.all()},
        "deletion_requests_pending": int(deletion_pending.scalar() or 0),
    }


# ── P4-B6 (Block 7): app suspension ──────────────────────────────────────────
class _SuspendBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/apps/{app_id}/suspend")
async def admin_suspend_app(
    app_id: str,
    body: _SuspendBody,
    request: Request,
    _admin: bool = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark an app as suspended.

    Side effect: every API key bound to ``app_id`` will start
    failing the ``check_app_not_suspended`` write dependency.
    Reads continue to work — see ``app/core/suspension_check.py``
    for the rationale.

    Audit row uses ``target_user_id = app.user_id`` (the audit
    schema requires a user FK). The app_id and the operator
    reason both live in the JSONB ``payload``.
    """
    try:
        app_uuid = uuid.UUID(app_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid app_id (must be a UUID)",
        )

    suspended_at = datetime.now(timezone.utc)

    if settings.demo_mode:
        # Demo path — no apps table. Store suspension state in the
        # parallel demo dict that the suspension-check dependency
        # reads from. There is no app.user_id we can audit here,
        # so we use a deterministic-but-clearly-fake target_user_id
        # so ``record_auth_event`` does not silently drop the row.
        from app import demo_db

        demo_db.demo_apps_suspension[str(app_uuid)] = {
            "suspended_at": suspended_at.isoformat(),
            "suspension_reason": body.reason,
        }
        target = str(app_uuid)  # audit row keyed on app_uuid as a UUID
        await record_auth_event(
            "app_suspended",
            target_user_id=target,
            request=request,
            payload={
                "actor": "admin",
                "app_id": str(app_uuid),
                "reason": body.reason,
            },
        )
        logger.info("admin.suspend_app: app_id=%s (demo)", app_uuid)
        return {
            "suspended": True,
            "app_id": str(app_uuid),
            "suspended_at": suspended_at.isoformat(),
        }

    # Production path
    from sqlalchemy import select

    result = await db.execute(select(App).where(App.id == app_uuid))
    app_row = result.scalar_one_or_none()
    if app_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="App not found"
        )
    await db.execute(
        update(App)
        .where(App.id == app_uuid)
        .values(suspended_at=suspended_at, suspension_reason=body.reason)
    )
    await db.commit()

    await record_auth_event(
        "app_suspended",
        target_user_id=app_row.user_id,
        request=request,
        payload={
            "actor": "admin",
            "app_id": str(app_uuid),
            "reason": body.reason,
        },
    )

    logger.info(
        "admin.suspend_app: app_id=%s user_id=%s",
        app_uuid,
        app_row.user_id,
    )
    return {
        "suspended": True,
        "app_id": str(app_uuid),
        "suspended_at": suspended_at.isoformat(),
    }


@router.post("/apps/{app_id}/unsuspend")
async def admin_unsuspend_app(
    app_id: str,
    request: Request,
    _admin: bool = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear an app's suspension state."""
    try:
        app_uuid = uuid.UUID(app_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid app_id (must be a UUID)",
        )

    if settings.demo_mode:
        from app import demo_db

        demo_db.demo_apps_suspension.pop(str(app_uuid), None)
        await record_auth_event(
            "app_unsuspended",
            target_user_id=str(app_uuid),
            request=request,
            payload={"actor": "admin", "app_id": str(app_uuid)},
        )
        logger.info("admin.unsuspend_app: app_id=%s (demo)", app_uuid)
        return {"unsuspended": True, "app_id": str(app_uuid)}

    from sqlalchemy import select

    result = await db.execute(select(App).where(App.id == app_uuid))
    app_row = result.scalar_one_or_none()
    if app_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="App not found"
        )
    await db.execute(
        update(App)
        .where(App.id == app_uuid)
        .values(suspended_at=None, suspension_reason=None)
    )
    await db.commit()

    await record_auth_event(
        "app_unsuspended",
        target_user_id=app_row.user_id,
        request=request,
        payload={"actor": "admin", "app_id": str(app_uuid)},
    )
    logger.info(
        "admin.unsuspend_app: app_id=%s user_id=%s",
        app_uuid,
        app_row.user_id,
    )
    return {"unsuspended": True, "app_id": str(app_uuid)}
