"""GDPR data export, deletion, and consent endpoints.

Phase 7 hardening (P7-E1, P7-E2):

* **P7-E1 streaming export.** ``GET /memory/user/{user_id}/export``
  used to load every row into memory and return a single JSON
  document. A user with a million episodes would OOM the worker
  before any response was written. The endpoint now streams an
  NDJSON document: the first line is a metadata envelope, every
  subsequent line is a single record carrying its ``kind``. Memory
  pressure on the server is bounded to a single ORM batch
  (``yield_per``) regardless of total user data volume.

* **P7-E2 single-transaction delete.**
  ``DELETE /memory/user/{user_id}/all`` now runs inside an explicit
  ``async with db.begin():`` block so any failure mid-chain rolls
  back the whole operation. All deletes use core ``delete()``
  statements consistently — no mixed ORM ``Session.delete`` calls
  that depend on session-state semantics.

* **Demo-mode parity.** Both routes also work against the in-memory
  demo store so the test suite can exercise them without Postgres,
  matching the pattern set by Phase 2 / Phase 3 routes.

* **Confirm-delete header is preserved** (a destructive operation
  should never be a one-click POST). The header value is compared
  case-insensitively to be friendly to operator tooling.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncIterator, Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import demo_db
from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.models.engram import Engram
from app.models.memory import (
    EpisodicMemory,
    KnowledgeEdge,
    KnowledgeNode,
    ProceduralMemory,
    SemanticMemory,
)
from app.models.user import APIKey, User

router = APIRouter(prefix="/memory/user", tags=["gdpr"])


# Order matters for delete: child rows first, then parents.
# knowledge_edges → knowledge_nodes (FK), engrams reference episodes
# in metadata only (no FK), so any order is safe across the rest.
_DELETE_ORDER = (
    Engram,
    SemanticMemory,
    EpisodicMemory,
    ProceduralMemory,
    KnowledgeEdge,
    KnowledgeNode,
)


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
    confirm: str | None = Header(None, alias="X-Confirm-Delete"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete every user-scoped row + the user record (P7-E2).

    The whole operation runs inside one explicit transaction. If any
    DELETE statement fails (FK violation, deadlock, dropped
    connection mid-flight), the partial state is rolled back and the
    client sees a 5xx without observable partial deletion.

    Authentication is invalidated by removing the user (cascade FKs
    on ``api_keys`` / ``refresh_tokens`` complete the cleanup) plus a
    belt-and-braces explicit DELETE on ``api_keys``.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=403, detail="Cannot delete another user's data"
        )
    if (confirm or "").lower() != "true":
        raise HTTPException(
            status_code=400, detail="Send X-Confirm-Delete: true to confirm"
        )

    if settings.demo_mode:
        return _delete_all_demo(user_id)

    delete_counts: dict[str, int] = {}

    # Single-transaction guarantee. ``async with db.begin()`` is a
    # no-op if a transaction is already open (autobegin), and a
    # full BEGIN/COMMIT otherwise.
    async with db.begin():
        for model in _DELETE_ORDER:
            result = await db.execute(
                delete(model).where(model.user_id == user_id)
            )
            delete_counts[model.__tablename__] = result.rowcount or 0

        api_key_result = await db.execute(
            delete(APIKey).where(APIKey.user_id == user_id)
        )
        delete_counts["api_keys"] = api_key_result.rowcount or 0

        # Final removal of the user row. The other tables have
        # ON DELETE CASCADE FK to users.id; we delete them explicitly
        # above so the rowcounts are observable in the response.
        user_result = await db.execute(
            delete(User).where(User.id == user_id)
        )
        delete_counts["users"] = user_result.rowcount or 0

    return {
        "deleted": True,
        "user_id": str(user_id),
        "authentication_invalidated": True,
        "deleted_counts": delete_counts,
    }


def _delete_all_demo(user_id: UUID) -> dict[str, Any]:
    """Demo-mode atomic delete: pop every store keyed on ``user_id``.

    Python dict mutations are atomic per-key under the GIL; we never
    hold partial state — either every store has been popped or none
    has, because we capture the pre-counts first and only pop after
    every count is captured.
    """
    uid = str(user_id)
    counts = {
        "episodic_memory": len(demo_db.episodic_store.get(uid, [])),
        "semantic_memory": len(demo_db.semantic_store.get(uid, [])),
        "procedural_memory": 1 if uid in demo_db.procedural_store else 0,
        "knowledge_nodes": len(demo_db.graph_nodes_store.get(uid, [])),
        "knowledge_edges": len(demo_db.graph_edges_store.get(uid, [])),
        "engrams": 0,
    }
    demo_db.episodic_store.pop(uid, None)
    demo_db.semantic_store.pop(uid, None)
    demo_db.procedural_store.pop(uid, None)
    demo_db.graph_nodes_store.pop(uid, None)
    demo_db.graph_edges_store.pop(uid, None)

    # Auth state: remove the user, the email index, every api key,
    # every refresh token, and any pending verification / reset
    # tokens for this user. Mirrors the cascade effect of deleting
    # the users row in production.
    user_record = demo_db.demo_users.pop(uid, None)
    api_key_count = 0
    if user_record is not None:
        email = (user_record.get("email") or "").lower()
        if email:
            demo_db.demo_users_by_email.pop(email, None)
    for key_id in [
        kid for kid, rec in demo_db.demo_api_keys.items()
        if str(rec["user_id"]) == uid
    ]:
        demo_db.demo_api_keys.pop(key_id, None)
        api_key_count += 1
    for token_hash in [
        h for h, rec in demo_db.demo_refresh_tokens.items()
        if str(rec["user_id"]) == uid
    ]:
        demo_db.demo_refresh_tokens.pop(token_hash, None)
    for token_hash in [
        h for h, rec in demo_db.demo_email_verification_tokens.items()
        if str(rec["user_id"]) == uid
    ]:
        demo_db.demo_email_verification_tokens.pop(token_hash, None)
    for token_hash in [
        h for h, rec in demo_db.demo_password_reset_tokens.items()
        if str(rec["user_id"]) == uid
    ]:
        demo_db.demo_password_reset_tokens.pop(token_hash, None)

    counts["api_keys"] = api_key_count
    counts["users"] = 1 if user_record is not None else 0
    return {
        "deleted": True,
        "user_id": uid,
        "authentication_invalidated": True,
        "deleted_counts": counts,
    }


@router.patch("/{user_id}/consent")
async def update_consent(
    user_id: UUID,
    flags: ConsentFlags,
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
    return {"updated": True, "user_id": str(user_id), "consent": consent}
