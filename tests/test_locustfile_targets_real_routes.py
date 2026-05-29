"""Regression guard: the load-test file must target real routes.

The previous locustfile.py POSTed to /api/v1/episodic/ and to /api/v1/rag/chat
with shapes the router rejects. The "load test" was load-testing 422 / 404.

This unit test parses the locustfile, extracts every URL it hits, and
asserts each one is registered on the FastAPI app. We do NOT actually
run Locust here.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app


LOCUSTFILE = Path(__file__).resolve().parent / "locustfile.py"


def _registered_paths() -> set[str]:
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


def _locustfile_paths() -> set[str]:
    """Grab every literal-string URL passed as the first positional arg of
    `self.client.<method>(<url>, ...)` calls in the locustfile.

    Templated parts like {self.user_id} are converted to the FastAPI path
    parameter `{user_id}` so the comparison matches.
    """
    src = LOCUSTFILE.read_text()
    # Match self.client.<verb>("/...") OR self.client.<verb>(f"/...").
    pattern = re.compile(
        r"""self\.client\.(?:get|post|put|delete|patch)\(\s*f?["']([^"']+)["']""",
        flags=re.DOTALL,
    )
    paths = set()
    for m in pattern.finditer(src):
        path = m.group(1)
        path = path.replace("{self.user_id}", "{user_id}")
        paths.add(path)
    return paths


def test_locustfile_paths_exist_on_the_app() -> None:
    locust_paths = _locustfile_paths()
    assert locust_paths, "no URLs detected in locustfile.py — pattern broken?"

    registered = _registered_paths()
    # /health/live is registered without the /api/v1 prefix.
    missing = []
    for p in locust_paths:
        # The locustfile uses /api/v1/* for app-level routes and /health/* for
        # health probes. Both must exist on the app.
        if p not in registered:
            missing.append(p)

    assert not missing, (
        f"locustfile.py points at routes that do not exist on the app: {missing}\n"
        f"registered routes: {sorted(registered)}"
    )