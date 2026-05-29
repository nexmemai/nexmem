"""App-scope consistency tests (R-H2).

R-H2 was a latent `AttributeError` in `app/routers/rag.py`: the
production path called `logger.info(..., app_id=current_user.app_id, ...)`
but the `User` model has no `app_id` attribute. The bug only manifests
on the second-to-last line of the request, so it would have produced a
500 on every successful production /rag/chat call once that branch was
exercised.

These tests guard against the regression at the source level so it
cannot be reintroduced by a careless paste:

  1. The `User` SQLAlchemy model must NOT declare an `app_id` column.
     (Decision documented in BACKEND_HARDENING_PHASE2.md: app scope
     comes from the request body / API key context only; the User
     model carries no app identity.)
  2. No router or service may dereference `current_user.app_id`,
     `user.app_id` (where `user` is a User instance), or any other
     pattern that assumes the User model has an `app_id` field.
  3. The Engram model now declares an `app_id` column (R-H5).
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"


# ── 1. User model must not have app_id ──────────────────────────────────────


def test_user_model_has_no_app_id() -> None:
    from app.models.user import User

    columns = {c.name for c in User.__table__.columns}
    assert "app_id" not in columns, (
        "User model must not declare an app_id column. App scope is request-"
        "level, not user-level. See BACKEND_HARDENING_PHASE2.md §P2-C3."
    )
    assert not hasattr(User, "app_id"), (
        "User class must not expose an app_id attribute (mapped or otherwise)."
    )


# ── 2. No router/service dereferences current_user.app_id or user.app_id ───


_FORBIDDEN_PATTERNS = (
    re.compile(r"\bcurrent_user\.app_id\b"),
    # `user.app_id` where `user` is the User object passed to a service.
    # We allow `user_id` so the pattern is intentionally narrow.
    re.compile(r"\buser\.app_id\b"),
    re.compile(r"\bUser\.app_id\b"),
)


def test_no_router_references_user_app_id() -> None:
    offenders: list[tuple[str, int, str]] = []
    for path in APP_ROOT.rglob("*.py"):
        text = path.read_text(errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern in _FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    offenders.append((rel, line_no, line.strip()))
    assert not offenders, (
        "No router or service may treat the User model as having an "
        "app_id field (R-H2). Use the request body's app_id instead.\n"
        + "\n".join(f"  {p}:{ln}: {snippet}" for p, ln, snippet in offenders)
    )


# ── 3. Engram model has app_id column (R-H5) ───────────────────────────────


def test_engram_model_has_app_id() -> None:
    from app.models.engram import Engram

    columns = {c.name for c in Engram.__table__.columns}
    assert "app_id" in columns, (
        "Engram model must declare an app_id column for multi-app scoping "
        "(R-H5). Migration 012_add_app_id_to_engrams adds the DB column."
    )


# ── 4. Routers consistently take app_id from the request ───────────────────


def test_rag_chat_logs_app_id_from_request() -> None:
    """The two `logger.info(...token_usage..., app_id=...)` calls in rag.py
    must source `app_id` from the request body, not from the User object.
    """
    src = (APP_ROOT / "routers" / "rag.py").read_text()
    # Both call sites must reference `request.app_id`. The legacy
    # `current_user.app_id` is forbidden by the test above; this asserts
    # the positive form.
    matches = re.findall(
        r"logger\.info\(\s*['\"]llm_token_usage['\"][^)]*app_id=([^,)\s]+)",
        src,
        flags=re.DOTALL,
    )
    assert len(matches) >= 2, (
        f"expected ≥ 2 llm_token_usage log calls in rag.py, found {len(matches)}"
    )
    for value in matches:
        assert value == "request.app_id", (
            f"app_id source in rag.py log call should be 'request.app_id', "
            f"got {value!r}. Tracking app_id from the request keeps the User "
            f"model app-agnostic."
        )
