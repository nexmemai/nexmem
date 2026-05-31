"""Per-app monthly usage tracking — Phase 4 (P4-B5, Block 7).

Two write entrypoints (``increment_app_write`` / ``increment_app_read``)
do an atomic upsert into ``app_usage``. One read entrypoint
(``get_app_usage``) returns the most recent N months for a single app
in a flat list.

Posture
-------
* The upsert is a single ``INSERT ... ON CONFLICT (app_id, month_year)
  DO UPDATE`` so two concurrent writers cannot race on the read /
  modify / write of an existing row. Postgres serialises the conflict
  resolution at the unique-constraint level.

* These functions are designed to be called from a request's
  background-task hook (FastAPI ``BackgroundTasks``). The two
  ``record_app_*`` helpers below open their own session and swallow
  every exception — they MUST NOT block the request response or
  cause a rollback on failure. Failures are logged at WARNING level
  only.

* In demo mode there is no Postgres, so the increment helpers
  manipulate ``demo_db.demo_app_usage`` directly. The dashboard
  endpoint also reads that store, so the demo path is fully
  exercised by the test suite without requiring a live database.

* RLS: the production session must have ``app.current_user_id`` set
  before the upsert runs, otherwise the migration-023 policy
  rejects the row. ``record_app_write`` / ``record_app_read``
  apply ``set_rls_context`` on every fresh session they open.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Union
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


logger = logging.getLogger(__name__)


# ── month_year helper ────────────────────────────────────────────────────────
def _month_year() -> str:
    """Return the current UTC month as ``YYYY-MM``.

    Matches the migration-023 column shape (``VARCHAR(7)``) so a
    typo here would break the unique-constraint anchor.
    """
    return datetime.utcnow().strftime("%Y-%m")


def _coerce_uuid(value: Union[UUID, str, None]) -> Optional[str]:
    """Normalise app_id / user_id arguments to a string form usable
    by raw SQL placeholders. Returns None for falsy input so callers
    can short-circuit on "no app context"."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    s = str(value).strip()
    return s or None


# ── Demo-mode helpers ────────────────────────────────────────────────────────
def _demo_increment(
    app_id: Union[UUID, str],
    user_id: Union[UUID, str],
    *,
    is_write: bool,
) -> None:
    """Bump the in-memory demo store for the current month.

    Mirrors the production upsert: same row keyed on
    ``(app_id, month_year)``; counter is created at 0 + 1 on the
    first call and incremented thereafter.
    """
    from app import demo_db

    aid = _coerce_uuid(app_id)
    uid = _coerce_uuid(user_id)
    if aid is None or uid is None:
        return
    month = _month_year()
    key = (aid, month)
    rec = demo_db.demo_app_usage.get(key)
    if rec is None:
        rec = {
            "app_id": aid,
            "user_id": uid,
            "month_year": month,
            "write_count": 0,
            "read_count": 0,
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }
        demo_db.demo_app_usage[key] = rec
    if is_write:
        rec["write_count"] += 1
    else:
        rec["read_count"] += 1
    rec["last_updated"] = datetime.utcnow().isoformat() + "Z"


# ── Public increment API (per spec — caller manages session/commit) ──────────
async def increment_app_write(
    db: AsyncSession,
    app_id: Union[UUID, str],
    user_id: Union[UUID, str],
) -> None:
    """Atomically bump ``write_count`` for the current month.

    Performs ``INSERT ... ON CONFLICT (app_id, month_year)
    DO UPDATE`` in a single round-trip. The migration-023
    UNIQUE constraint is the conflict anchor.

    Caller owns the session lifecycle. ``record_app_write`` is the
    fire-and-forget wrapper used by the request hot path.
    """
    if settings.demo_mode:
        _demo_increment(app_id, user_id, is_write=True)
        return

    aid = _coerce_uuid(app_id)
    uid = _coerce_uuid(user_id)
    if aid is None or uid is None:
        return

    await db.execute(
        text(
            """
            INSERT INTO app_usage
                (id, app_id, user_id, month_year,
                 write_count, read_count, last_updated)
            VALUES
                (gen_random_uuid(), :app_id, :user_id,
                 :month_year, 1, 0, now())
            ON CONFLICT (app_id, month_year)
            DO UPDATE SET
                write_count = app_usage.write_count + 1,
                last_updated = now()
            """
        ),
        {"app_id": aid, "user_id": uid, "month_year": _month_year()},
    )
    await db.commit()


async def increment_app_read(
    db: AsyncSession,
    app_id: Union[UUID, str],
    user_id: Union[UUID, str],
) -> None:
    """Atomically bump ``read_count`` for the current month."""
    if settings.demo_mode:
        _demo_increment(app_id, user_id, is_write=False)
        return

    aid = _coerce_uuid(app_id)
    uid = _coerce_uuid(user_id)
    if aid is None or uid is None:
        return

    await db.execute(
        text(
            """
            INSERT INTO app_usage
                (id, app_id, user_id, month_year,
                 write_count, read_count, last_updated)
            VALUES
                (gen_random_uuid(), :app_id, :user_id,
                 :month_year, 0, 1, now())
            ON CONFLICT (app_id, month_year)
            DO UPDATE SET
                read_count = app_usage.read_count + 1,
                last_updated = now()
            """
        ),
        {"app_id": aid, "user_id": uid, "month_year": _month_year()},
    )
    await db.commit()


# ── Read API ─────────────────────────────────────────────────────────────────
async def get_app_usage(
    db: AsyncSession,
    app_id: Union[UUID, str],
    months: int = 4,
) -> list[dict]:
    """Return the most recent ``months`` records for one app, newest first.

    Caller is expected to have already verified the user owns the
    app — this function does not do its own ownership check. RLS
    on ``app_usage`` provides defense-in-depth: a request that
    forgets to set ``app.current_user_id`` will see zero rows.

    Demo mode reads the in-memory store and applies the same
    "newest first / limit N" shape so callers do not need to know
    the backend.
    """
    aid = _coerce_uuid(app_id)
    if aid is None:
        return []

    if settings.demo_mode:
        from app import demo_db

        rows = [
            rec
            for (rec_app_id, _month), rec in demo_db.demo_app_usage.items()
            if rec_app_id == aid
        ]
        rows.sort(key=lambda r: r["month_year"], reverse=True)
        return [
            {
                "app_id": r["app_id"],
                "month_year": r["month_year"],
                "write_count": r["write_count"],
                "read_count": r["read_count"],
                "last_updated": r["last_updated"],
            }
            for r in rows[:months]
        ]

    result = await db.execute(
        text(
            """
            SELECT app_id, month_year,
                   write_count, read_count, last_updated
            FROM app_usage
            WHERE app_id = :app_id
            ORDER BY month_year DESC
            LIMIT :months
            """
        ),
        {"app_id": aid, "months": int(months)},
    )
    return [dict(row._mapping) for row in result.fetchall()]


# ── Fire-and-forget wrappers for the request hot path ────────────────────────
async def record_app_write(
    app_id: Union[UUID, str],
    user_id: Union[UUID, str],
) -> None:
    """Fire-and-forget counterpart of ``increment_app_write``.

    Opens its own ``async_session()`` so it cannot interfere with
    the request's transaction. Catches every exception — an app-
    metrics failure must never poison a successful write. Logs at
    WARNING level so an operator still sees the trend if it
    becomes systemic.

    The outer try/except wraps both the demo and production paths
    so a hypothetical store-corruption bug in demo mode is also
    swallowed (the spec contract is "never block the response",
    not "never block the response in production").
    """
    aid = _coerce_uuid(app_id)
    uid = _coerce_uuid(user_id)
    if aid is None or uid is None:
        return

    try:
        if settings.demo_mode:
            _demo_increment(aid, uid, is_write=True)
            return

        # Local import: defer database engine touch until we are
        # actually about to use it. Same pattern as audit_log.py.
        from app.database import async_session, set_rls_context

        async with async_session() as session:
            # The migration-023 policy needs current_user_id set
            # before the INSERT can pass WITH CHECK.
            await set_rls_context(session, uid, aid)
            await increment_app_write(session, aid, uid)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "app_usage: write increment failed for app_id=%s user_id=%s: %s",
            aid,
            uid,
            exc,
        )


async def record_app_read(
    app_id: Union[UUID, str],
    user_id: Union[UUID, str],
) -> None:
    """Fire-and-forget counterpart of ``increment_app_read``."""
    aid = _coerce_uuid(app_id)
    uid = _coerce_uuid(user_id)
    if aid is None or uid is None:
        return

    try:
        if settings.demo_mode:
            _demo_increment(aid, uid, is_write=False)
            return

        from app.database import async_session, set_rls_context

        async with async_session() as session:
            await set_rls_context(session, uid, aid)
            await increment_app_read(session, aid, uid)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "app_usage: read increment failed for app_id=%s user_id=%s: %s",
            aid,
            uid,
            exc,
        )
