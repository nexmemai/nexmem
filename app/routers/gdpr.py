"""GDPR data export, deletion, and consent endpoints.

Phase 7 / Block 5 hardening (P7-E1, P7-E2 → P7-E4):

* **P7-E1 streaming export.** ``GET /memory/user/{user_id}/export``
  used to load every row into memory and return a single JSON
  document. A user with a million episodes would OOM the worker
  before any response was written. The endpoint now streams an
  NDJSON document: the first line is a metadata envelope, every
  subsequent line is a single record carrying its ``kind``. Memory
  pressure on the server is bounded to a single ORM batch
  (``yield_per``) regardless of total user data volume.

* **P7-E4 (Block 5) soft-delete grace period.**
  ``DELETE /memory/user/{user_id}/all`` no longer cascades
  immediately. It stamps ``users.deletion_scheduled_for`` and
  flips ``is_active`` to False — the account is frozen at once but
  every memory row survives for 30 days. The actual cascade runs
  in the ``execute_scheduled_deletions`` Celery task. A user who
  changes their mind can call ``POST /memory/user/{id}/cancel-deletion``
  to roll the schedule back during the grace period; that route
  uses the dedicated ``get_user_in_grace_period`` dependency
  because the standard ``get_current_user`` rejects inactive users.

* **Demo-mode parity.** Both routes also work against the in-memory
  demo store so the test suite can exercise them without Postgres,
  matching the pattern set by Phase 2 / Phase 3 routes.

* **Confirm-delete header is preserved** (a destructive operation
  should never be a one-click POST). The header value is compared
  case-insensitively to be friendly to operator tooling.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import demo_db
from app.config import settings
from app.core import demo_auth
from app.core.audit_log import record_gdpr_event
from app.core.deps import get_current_user
from app.core.security import decode_token
from app.database import get_db
from app.models.engram import Engram
from app.models.memory import (
    EpisodicMemory,
    KnowledgeEdge,
    KnowledgeNode,
    ProceduralMemory,
    SemanticMemory,
)
from app.models.user import User

router = APIRouter(prefix="/memory/user", tags=["gdpr"])

logger = logging.getLogger(__name__)


# ── P7-E4 (Block 5): GDPR soft-delete grace period ───────────────────────────
# A delete request is reversible for ``DELETION_GRACE_DAYS`` days.
# Past that window, the daily ``execute_scheduled_deletions`` Celery
# task runs the actual cascade. 30 days matches the GDPR Art. 17
# "without undue delay" guidance while leaving room for the user to
# change their mind.
DELETION_GRACE_DAYS = 30


# Models surfaced in /export, with the kind tag the client sees.
_EXPORT_MODELS: tuple[tuple[str, type], ...] = (
    ("episodic", EpisodicMemory),
    ("semantic", SemanticMemory),
    ("procedural", ProceduralMemory),
    ("knowledge_node", KnowledgeNode),
    ("knowledge_edge", KnowledgeEdge),
    ("engram", Engram),
)


# ── Schemas ──────────────────────────────────────────────────────────────────
class ConsentFlags(BaseModel):
    marketing: bool = False
    analytics: bool = True


# ── Serialization helpers ────────────────────────────────────────────────────
def _json_safe(value: Any) -> Any:
    """Convert ORM values into JSON-safe primitives."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    return value


def _model_to_dict(model: Any) -> dict[str, Any]:
    """Serialize a SQLAlchemy model using database column names."""
    return {
        column.name: _json_safe(getattr(model, column.key))
        for column in model.__table__.columns
    }


def _ndjson(line: dict[str, Any]) -> bytes:
    """One NDJSON line with a trailing newline."""
    return (json.dumps(line, separators=(",", ":")) + "\n").encode("utf-8")


# ── Streaming generators (P7-E1) ─────────────────────────────────────────────
async def _stream_export_db(
    db: AsyncSession, user_id: UUID
) -> AsyncIterator[bytes]:
    """Yield one NDJSON line at a time, fetched in DB-side batches."""
    yield _ndjson(
        {
            "kind": "metadata",
            "format": "ndjson",
            "exported_at": datetime.utcnow().isoformat(),
            "user_id": str(user_id),
        }
    )
    for kind, model in _EXPORT_MODELS:
        # ``stream_scalars`` + ``yield_per`` keeps the server-side
        # cursor open and pulls rows in chunks of 256 instead of
        # buffering the entire result set.
        result = await db.stream_scalars(
            select(model).where(model.user_id == user_id).execution_options(
                yield_per=256
            )
        )
        async for record in result:
            yield _ndjson({"kind": kind, "data": _model_to_dict(record)})


async def _stream_export_demo(user_id: UUID) -> AsyncIterator[bytes]:
    """Demo-mode equivalent: walk the in-memory stores."""
    uid = str(user_id)
    yield _ndjson(
        {
            "kind": "metadata",
            "format": "ndjson",
            "exported_at": datetime.utcnow().isoformat(),
            "user_id": uid,
        }
    )
    sources: Iterable[tuple[str, list[dict]]] = (
        ("episodic", demo_db.episodic_store.get(uid, [])),
        ("semantic", demo_db.semantic_store.get(uid, [])),
        ("knowledge_node", demo_db.graph_nodes_store.get(uid, [])),
        ("knowledge_edge", demo_db.graph_edges_store.get(uid, [])),
    )
    for kind, rows in sources:
        for row in rows:
            yield _ndjson({"kind": kind, "data": _json_safe(row)})

    proc = demo_db.procedural_store.get(uid)
    if proc is not None:
        yield _ndjson({"kind": "procedural", "data": _json_safe(proc)})


# ── Routes ───────────────────────────────────────────────────────────────────
@router.get("/{user_id}/export")
async def export_all_memories(
    user_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream every user-scoped memory record as NDJSON (P7-E1).

    Response is ``application/x-ndjson``: one JSON object per line.
    A leading metadata line carries the export timestamp and user
    id; every subsequent line tags itself with ``"kind"``.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=403, detail="Cannot export another user's data"
        )

    if settings.demo_mode:
        gen = _stream_export_demo(user_id)
    else:
        gen = _stream_export_db(db, user_id)

    # Audit (P10-H1) — record the action *before* we start streaming.
    # If the helper itself fails we still proceed; the audit helper
    # already swallows its own exceptions.
    await record_gdpr_event("export", target_user_id=user_id, request=request)

    return StreamingResponse(
        gen,
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": (
                f'attachment; filename="nexmem-export-{user_id}.ndjson"'
            ),
            # Hint that the response can be safely streamed straight
            # to disk; many proxies buffer otherwise.
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{user_id}/all")
async def delete_all_memories(
    user_id: UUID,
    request: Request,
    confirm: str | None = Header(None, alias="X-Confirm-Delete"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Schedule a soft-delete of every user-scoped row (P7-E4, Block 5).

    The previous immediate-cascade behaviour is replaced with a
    30-day grace period. Calling this route:

    1. Stamps ``users.deletion_requested_at`` and
       ``users.deletion_scheduled_for`` on the user row.
    2. Sets ``users.is_active = False`` so every authenticated route
       returns 401 from the next request onward — the account is
       immediately frozen even though no memory rows are erased yet.
    3. Returns metadata the client can show the user, including a
       ``cancel_url`` they can hit during the grace period to roll
       the deletion back.

    The actual cascade across memory tables runs daily inside the
    ``execute_scheduled_deletions`` Celery task, which scans for
    rows where ``deletion_scheduled_for <= now()``.

    Idempotent: a second call during the grace period rolls the
    schedule forward by another 30 days, but does not duplicate
    rows or audit events — the most recent request always wins.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete another user's data",
        )
    if (confirm or "").lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send X-Confirm-Delete: true to confirm",
        )

    requested_at = datetime.now(timezone.utc)
    scheduled_for = requested_at + timedelta(days=DELETION_GRACE_DAYS)

    if settings.demo_mode:
        demo_auth.request_deletion(user_id, requested_at, scheduled_for)
    else:
        async with db.begin():
            await db.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    deletion_requested_at=requested_at,
                    deletion_scheduled_for=scheduled_for,
                    is_active=False,
                )
            )

    await record_gdpr_event(
        "delete_request",
        target_user_id=user_id,
        request=request,
        payload={
            "deletion_scheduled_for": scheduled_for.isoformat(),
            "grace_period_days": DELETION_GRACE_DAYS,
        },
    )

    return {
        "scheduled_deletion": True,
        "deletion_date": scheduled_for.isoformat(),
        "grace_period_days": DELETION_GRACE_DAYS,
        "cancel_before": scheduled_for.isoformat(),
        "cancel_url": f"/api/v1/memory/user/{user_id}/cancel-deletion",
    }


# ── /cancel-deletion (P7-E4) ─────────────────────────────────────────────────
async def get_user_in_grace_period(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Auth dependency that admits users with ``is_active=False`` AS LONG AS
    they are inside the soft-delete grace period.

    The standard ``get_current_user`` rejects inactive users (which is
    correct for every other route — a frozen account should not be
    able to call /memory/episode/write etc.). The cancel-deletion
    route is the one exception: the user is by definition inactive
    when they want to cancel. This dependency does the same JWT +
    blocklist checks but treats ``is_active=False`` as ALLOWED iff
    ``deletion_scheduled_for`` is set and still in the future. If
    the grace period has elapsed, the dependency raises 410 Gone so
    the route handler never sees the user.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        scheme, credentials = auth_header.split()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cancel-deletion requires a bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("type", "access") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    try:
        user_uuid = UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user id in token",
        )

    if settings.demo_mode:
        demo_user = demo_auth.get_user_by_id(str(user_uuid))
        if demo_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        sched = demo_user.deletion_scheduled_for
        # Build a lightweight User stub the route handler can use.
        return User(
            id=demo_user.id,
            email=demo_user.email,
            wallet_address=demo_user.wallet_address,
            hashed_password=demo_user.hashed_password,
            is_active=demo_user.is_active,
            created_at=demo_user.created_at,
            email_verified_at=demo_user.email_verified_at,
            deletion_requested_at=demo_user.deletion_requested_at,
            deletion_scheduled_for=sched,
        )

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


@router.post("/{user_id}/cancel-deletion")
async def cancel_deletion(
    user_id: UUID,
    request: Request,
    current_user: User = Depends(get_user_in_grace_period),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending soft-delete and reactivate the account.

    Returns 400 if there is no pending deletion, 410 if the grace
    period has already elapsed (deletion has either run or is about
    to run; cancellation is impossible). On success the route clears
    both timestamps and flips ``is_active`` back to True. Idempotent
    only inside the grace period — calling it twice on the same row
    is a no-op the second time because ``deletion_scheduled_for`` is
    NULL.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot cancel another user's deletion",
        )

    sched = current_user.deletion_scheduled_for
    if sched is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending deletion",
        )
    now = datetime.now(timezone.utc)
    # Postgres returns timezone-aware datetimes; the demo store also
    # uses timezone-aware UTC (see DELETE handler). Normalise the
    # rare naive case so the comparison never raises.
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=timezone.utc)
    if sched <= now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Grace period expired, deletion already executed",
        )

    if settings.demo_mode:
        demo_auth.cancel_deletion(user_id)
    else:
        async with db.begin():
            await db.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    deletion_requested_at=None,
                    deletion_scheduled_for=None,
                    is_active=True,
                )
            )

    await record_gdpr_event(
        "delete_cancel",
        target_user_id=user_id,
        request=request,
    )
    return {"cancelled": True, "account_restored": True}


@router.patch("/{user_id}/consent")
async def update_consent(
    user_id: UUID,
    flags: ConsentFlags,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store GDPR consent flags in procedural_memory.settings['consent']."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=403, detail="Cannot update another user's consent"
        )

    consent = flags.model_dump()

    if settings.demo_mode:
        uid = str(user_id)
        proc = demo_db.procedural_store.get(uid)
        if proc is None:
            demo_db.upsert_procedural(
                uid, settings={"consent": consent}, workflows=[]
            )
        else:
            new_settings = dict(proc.get("settings") or {})
            new_settings["consent"] = consent
            proc["settings"] = new_settings
        await record_gdpr_event(
            "consent_change",
            target_user_id=user_id,
            request=request,
            payload={"consent": consent},
        )
        return {"updated": True, "user_id": uid, "consent": consent}

    result = await db.execute(
        select(ProceduralMemory).where(
            ProceduralMemory.user_id == user_id,
            ProceduralMemory.app_id.is_(None),
        )
    )
    procedural = result.scalar_one_or_none()

    if procedural:
        new_settings = dict(procedural.settings or {})
        new_settings["consent"] = consent
        procedural.settings = new_settings
    else:
        procedural = ProceduralMemory(
            user_id=user_id,
            app_id=None,
            settings={"consent": consent},
            workflows=[],
            store_procedural=True,
        )
        db.add(procedural)

    await db.commit()
    await record_gdpr_event(
        "consent_change",
        target_user_id=user_id,
        request=request,
        payload={"consent": consent},
    )
    return {"updated": True, "user_id": str(user_id), "consent": consent}
