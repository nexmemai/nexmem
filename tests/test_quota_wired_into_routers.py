"""Regression guard: the four write routers must depend on enforce_write_quota.

If a future refactor replaces `Depends(enforce_write_quota)` with a bare
`Depends(get_current_user)` on any of these routes, this test fails. That
is the entire point — the audit found that the previous quota function
was defined but never invoked.

We inspect the FastAPI app's routes directly. We do NOT issue HTTP requests
because the demo-mode auth dependency would short-circuit before quota.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.core.quota import enforce_write_quota
from app.main import app


def _route_dependencies(method: str, path: str):
    """Return the set of dependant call objects on the named route."""
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path == path and method in r.methods:
            # FastAPI flattens the dependency tree into r.dependant.dependencies
            return r.dependant
    raise AssertionError(f"route not found: {method} {path}")


def _route_uses_dependency(method: str, path: str, dep_callable) -> bool:
    """True if the route's dependency tree contains the given callable."""
    dependant = _route_dependencies(method, path)
    stack = list(dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is dep_callable:
            return True
        stack.extend(d.dependencies)
    return False


# ── Each protected write endpoint must enforce the write quota ──────────────

def test_episodic_create_enforces_quota() -> None:
    assert _route_uses_dependency(
        "POST", "/api/v1/agents/{user_id}/episodes", enforce_write_quota
    ), "POST /agents/{user_id}/episodes is not gated by enforce_write_quota"


def test_semantic_create_enforces_quota() -> None:
    assert _route_uses_dependency(
        "POST", "/api/v1/agents/{user_id}/semantics", enforce_write_quota
    ), "POST /agents/{user_id}/semantics is not gated by enforce_write_quota"


def test_unified_episode_write_enforces_quota() -> None:
    assert _route_uses_dependency(
        "POST", "/api/v1/memory/episode/write", enforce_write_quota
    ), "POST /memory/episode/write is not gated by enforce_write_quota"


def test_rag_chat_enforces_quota() -> None:
    assert _route_uses_dependency(
        "POST", "/api/v1/rag/chat", enforce_write_quota
    ), "POST /rag/chat is not gated by enforce_write_quota"
