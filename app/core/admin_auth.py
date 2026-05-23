"""Admin authentication dependency (P11-I2/I3/I4, Block 6).

Single primitive: ``get_admin_user`` — a FastAPI ``Depends`` that
gates every route under ``/api/v1/admin/*`` behind a static
``X-Admin-Key`` header.

Posture
-------
This is **not** an end-user authentication mechanism. The admin key
is a static, opaque secret held by operators (not by any registered
user). It mirrors the way a small SaaS gates "danger" endpoints
during the private-beta phase: simple, auditable, and trivial to
rotate by editing the deploy environment.

* Unset ``ADMIN_API_KEY`` → every admin route returns **501 Not
  Implemented**. This is the default and keeps the surface inert
  unless the operator opts in.
* Set + missing header on a request → **401**.
* Set + wrong header value → **403**.
* Set + correct header value → the dependency returns ``True``
  and the route handler runs.

Comparison uses ``hmac.compare_digest`` to avoid leaking the key
length / contents through a timing side channel. The header value
is never logged: ``Authorization`` and ``X-Admin-Key`` are both
in the existing Sentry scrubbing list (see ``app.main``).

The dependency intentionally returns ``bool`` rather than a
``User`` because the admin caller has no user identity. Routes
that need a target user resolve them by URL parameter, never by
``current_user``.
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import settings


async def get_admin_user(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> bool:
    """Validate the X-Admin-Key header.

    Raises 501 / 401 / 403 with the contract documented at the top
    of this module. Returns ``True`` on success.
    """
    if not settings.admin_api_key:
        # Inert by default. 501 (rather than 404) so operators
        # explicitly know the surface exists but is disabled.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Admin endpoints are not configured (ADMIN_API_KEY unset)",
        )
    if not x_admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin key required",
            headers={"WWW-Authenticate": "X-Admin-Key"},
        )
    # Constant-time compare. The two strings are byte-encoded so
    # ``compare_digest`` never short-circuits on the (rare) case
    # where one side is shorter than the other.
    if not hmac.compare_digest(
        x_admin_key.encode("utf-8"),
        settings.admin_api_key.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin key",
        )
    return True
