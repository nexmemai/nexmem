"""Helpers for writing to the audit-log tables (P10-H1, P10-H2).

Two contracts the rest of the app depends on:

1. **Best-effort writes.** Audit-log writes never raise to the
   request handler. A failed write is logged as ``error`` to
   structlog and Sentry; the original action still succeeds.
   Otherwise an audit-table outage would block normal traffic.

2. **Demo parity.** In ``DEMO_MODE`` the helpers append to two
   in-memory lists keyed on the user id, so the full integration
   test suite can exercise the audit pipeline without Postgres.

Both helpers run in a fresh ``async_session()`` so they do not
disturb the caller's transaction state — important because
``/auth/login`` and ``/memory/user/{id}/delete`` already wrap
business logic in ``async with db.begin():`` blocks.

Signatures:

* ``record_auth_event(action, *, target_user_id, actor_user_id=None,
  request=None, payload=None)``
* ``record_gdpr_event(action, *, target_user_id, actor_user_id=None,
  request=None, payload=None)``

``actor_user_id`` defaults to ``target_user_id`` when omitted (the
common self-action case). Pass an explicit value for future
support-impersonation events (P11-I2).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Request

from app.config import settings


logger = logging.getLogger(__name__)


# ── Demo-mode in-memory store ────────────────────────────────────────────────
_demo_auth_log: Dict[str, list] = {}
_demo_gdpr_log: Dict[str, list] = {}


def reset_demo_audit_log() -> None:
    """Wipe the demo-mode stores. Safe to call between tests."""
    _demo_auth_log.clear()
    _demo_gdpr_log.clear()


def list_demo_auth_events(target_user_id: str) -> list:
    return list(_demo_auth_log.get(str(target_user_id), []))


def list_demo_gdpr_events(target_user_id: str) -> list:
    return list(_demo_gdpr_log.get(str(target_user_id), []))


# ── Public API ───────────────────────────────────────────────────────────────
def _client_meta(request: Optional[Request]) -> Dict[str, Optional[str]]:
    if request is None:
        return {"ip_address": None, "user_agent": None, "request_id": None}
    ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    user_agent = request.headers.get("User-Agent")
    request_id = getattr(request.state, "request_id", None)
    return {
        "ip_address": ip or None,
        "user_agent": user_agent,
        "request_id": request_id,
    }


def _coerce_uid(value) -> Optional[uuid.UUID]:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _write_event(
    table_name: str,
    *,
    action: str,
    target_user_id,
    actor_user_id=None,
    request: Optional[Request] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Common write path for both audit tables.

    ``table_name`` is one of ``auth_audit_log`` /``gdpr_audit_log``;
    we route to the correct ORM model so the caller does not need to
    import them.
    """
    target = _coerce_uid(target_user_id)
    if target is None:
        # Audit row makes no sense without a target.
        logger.warning(
            "audit_log: dropping %s/%s — invalid target_user_id %r",
            table_name,
            action,
            target_user_id,
        )
        return

    actor = _coerce_uid(actor_user_id) or target
    meta = _client_meta(request)
    payload_clean = dict(payload or {})

    if settings.demo_mode:
        record = {
            "id": str(uuid.uuid4()),
            "actor_user_id": str(actor),
            "target_user_id": str(target),
            "action": action,
            "payload": payload_clean,
            **meta,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        store = (
            _demo_auth_log
            if table_name == "auth_audit_log"
            else _demo_gdpr_log
        )
        store.setdefault(str(target), []).append(record)
        return

    # Production write. We open a fresh session so we never collide
    # with a transaction the caller has open. RLS context is set to
    # the actor so the WITH CHECK clause passes.
    try:
        from app.database import async_session, set_rls_context
        from app.models.audit_log import AuthAuditLog, GDPRAuditLog

        Model = (
            AuthAuditLog if table_name == "auth_audit_log" else GDPRAuditLog
        )

        async with async_session() as session:
            await set_rls_context(session, str(actor))
            session.add(
                Model(
                    actor_user_id=actor,
                    target_user_id=target,
                    action=action,
                    payload=payload_clean,
                    ip_address=meta["ip_address"],
                    user_agent=meta["user_agent"],
                    request_id=meta["request_id"],
                )
            )
            await session.commit()
    except Exception as exc:
        # Fail open — never let an audit failure cascade into the
        # business action. The structured logger captures the
        # event; Sentry catches the exception.
        logger.error(
            "audit_log: write failed for %s/%s target=%s: %s",
            table_name,
            action,
            target,
            exc,
        )


async def record_auth_event(
    action: str,
    *,
    target_user_id,
    actor_user_id=None,
    request: Optional[Request] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    await _write_event(
        "auth_audit_log",
        action=action,
        target_user_id=target_user_id,
        actor_user_id=actor_user_id,
        request=request,
        payload=payload,
    )


async def record_gdpr_event(
    action: str,
    *,
    target_user_id,
    actor_user_id=None,
    request: Optional[Request] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    await _write_event(
        "gdpr_audit_log",
        action=action,
        target_user_id=target_user_id,
        actor_user_id=actor_user_id,
        request=request,
        payload=payload,
    )
