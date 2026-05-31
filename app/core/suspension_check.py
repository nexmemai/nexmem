"""App-suspension write guard (P4-B6, Block 7).

A FastAPI dependency that 403s a write request when its API key is
bound to an app row that the operator has suspended. Read routes
must NOT use this dependency — a suspended user is still allowed
to read (and export) their own data, which is the explicit Phase 4
posture.

Sourcing the app_id
-------------------
The User ORM model has no ``app_id`` attribute. App scope for an
authenticated request lives on ``request.state.current_app_id``,
populated by the API-key auth path in ``app/core/deps.py``. JWT
auth leaves it ``None``, in which case this dependency is a no-op
(JWT-authenticated requests are not bound to an app and therefore
cannot be suspended).

Demo mode
---------
Reads ``demo_db.demo_apps_suspension`` — a parallel store keyed on
app_id. The admin suspend / unsuspend routes write to it; this
dependency reads from it.

Audit / failure mode
--------------------
* Suspended app: 403 with a structured detail body — `error`,
  user-facing `message`, and the app_id so an operator UI can show
  context.
* Unknown app_id (FK was set but the app row was hard-deleted):
  treat as not-suspended. The deletion already revoked access
  through the FK SET NULL on api_keys; we do not double-fault
  here.
* DB error: log a warning and **fail open**. Suspension is a
  defensive layer; a database hiccup must not turn into an
  outage. This matches the R-301 fail-open posture documented
  in BACKEND_RISKS.md for adjacent guard subsystems.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.apps import App


logger = logging.getLogger(__name__)


def _request_app_id(request: Request) -> Optional[str]:
    """Pull the request-scoped app_id off ``request.state``.

    Falls through to ``None`` when the auth path did not set the
    attribute (JWT auth, demo mode without app context, internal
    health probes that bypass auth).
    """
    state = getattr(request, "state", None)
    if state is None:
        return None
    aid = getattr(state, "current_app_id", None)
    if not aid:
        return None
    return str(aid)


async def check_app_not_suspended(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Dependency: raise 403 if the request's app is suspended.

    No-op when the request has no app context. Returns ``None`` on
    success.
    """
    app_id = _request_app_id(request)
    if not app_id:
        return

    if settings.demo_mode:
        from app import demo_db

        rec = demo_db.demo_apps_suspension.get(str(app_id))
        if rec and rec.get("suspended_at"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "app_suspended",
                    "message": (
                        "This application has been suspended. "
                        "Contact support."
                    ),
                    "app_id": str(app_id),
                },
            )
        return

    # Production: PK lookup on ``apps`` for the suspension columns.
    # The operator-suspended state is rare; we do not cache, the
    # primary-key lookup is cheap, and a stale cache on an
    # adversarial-control endpoint is worse than a tiny per-request
    # cost.
    try:
        result = await db.execute(
            select(App.suspended_at).where(App.id == app_id)
        )
        suspended_at = result.scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        # Fail open — see module docstring.
        logger.warning(
            "suspension_check: lookup failed for app_id=%s: %s",
            app_id,
            exc,
        )
        return

    if suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "app_suspended",
                "message": (
                    "This application has been suspended. "
                    "Contact support."
                ),
                "app_id": str(app_id),
            },
        )
