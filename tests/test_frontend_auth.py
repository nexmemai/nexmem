"""Frontend auth integration audit tests (P12-J5, Block 8).

The Streamlit dashboard at ``frontend/app.py`` is the operator-facing
surface. The auth contract for that surface is:

* The API base URL must be configurable via the ``API_BASE_URL`` env
  var (or Streamlit secrets) — never hardcoded to localhost in the
  prod path.
* The auth token must come from a UI input or env var, not from a
  literal in source.
* When an auth token IS present, every API call must carry an
  ``Authorization`` header. The Streamlit app supports both
  ``Bearer <jwt>`` and ``ApiKey <nxm_…>`` shapes by inspecting the
  token prefix.
* No hardcoded API keys or JWTs should appear in the source.

These are static checks — we read the source file and look for the
expected patterns. Importing ``frontend/app.py`` would run the
Streamlit page-config code, which is undesirable inside the test
process. The static-analysis posture is enough for an MVP-grade
audit; deeper end-to-end coverage belongs in a Selenium / Playwright
job that the operator owns.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit]


_FRONTEND_PATH = Path(__file__).parent.parent / "frontend" / "app.py"


@pytest.fixture(scope="module")
def app_source() -> str:
    """Read the Streamlit dashboard source once per test module."""
    if not _FRONTEND_PATH.exists():
        pytest.skip(f"{_FRONTEND_PATH} not present in this checkout")
    return _FRONTEND_PATH.read_text(encoding="utf-8")


def test_streamlit_uses_env_var_for_api_base(app_source: str):
    """``API_BASE`` must be sourced from ``os.environ`` (or st.secrets),
    never hardcoded to localhost in the prod path."""
    # The exact line in the source. Anchored so a future refactor that
    # silently inlines a literal fails the test.
    assert "os.environ.get(\"API_BASE_URL\")" in app_source, (
        "frontend/app.py must read API_BASE_URL from os.environ — "
        "hardcoded production URL detected."
    )
    # The fallback is allowed to be a localhost dev URL — that's the
    # right default when running locally — but it must come AFTER the
    # env var lookup, not replace it.
    env_idx = app_source.index("os.environ.get(\"API_BASE_URL\")")
    fallback_match = re.search(r'http://localhost:\d+', app_source[env_idx:])
    assert fallback_match is not None, (
        "expected a localhost fallback after the env var lookup"
    )


def test_streamlit_uses_input_field_for_auth_token(app_source: str):
    """The auth token must come from a UI input (st.text_input), not
    from a literal in source. A bare ``auth_token = "nxm_..."`` would
    be a security regression."""
    # The Streamlit input pattern.
    assert "st.text_input(" in app_source
    # Ensure the input is wired to a variable named auth_token.
    assert re.search(
        r"auth_token\s*=\s*st\.text_input\(",
        app_source,
    ), "auth_token must be read from st.text_input"


def test_streamlit_sends_authorization_header_when_token_present(
    app_source: str,
):
    """Both api_get and api_post must inject Authorization when
    auth_token is truthy. We assert both helpers contain the
    conditional and the matching header set."""
    # The backend accepts both Bearer JWTs and ApiKey nxm_… tokens.
    # The frontend must support both shapes.
    assert 'Authorization"] = f"ApiKey {auth_token}"' in app_source, (
        "ApiKey auth header missing"
    )
    assert 'Authorization"] = f"Bearer {auth_token}"' in app_source, (
        "Bearer auth header missing"
    )
    # Both api_get and api_post must use the conditional, not just one.
    api_get_idx = app_source.index("def api_get(")
    api_post_idx = app_source.index("def api_post(")
    api_get_body = app_source[api_get_idx:api_post_idx]
    api_post_body = app_source[api_post_idx:]
    for name, body in (("api_get", api_get_body), ("api_post", api_post_body)):
        assert "if auth_token:" in body, (
            f"{name} must guard auth header on truthy auth_token"
        )
        assert "Authorization" in body, (
            f"{name} must set Authorization header"
        )


def test_streamlit_no_hardcoded_credentials(app_source: str):
    """No literal API keys or JWTs in source. We allow the
    deterministic demo user UUID (``7e082e59-…``) because that is a
    placeholder default for the user-id input field, not a credential."""
    # nxm_ prefix indicates a real API key. ``nxm_your_key_here`` style
    # placeholders are out of scope — we only fail on key shapes that
    # look real (>= 16 char body after the prefix).
    real_key_pattern = re.compile(r"nxm_[A-Za-z0-9_-]{16,}")
    matches = real_key_pattern.findall(app_source)
    # Filter out placeholder-y matches: real keys are typically 32+ chars
    # of base64-ish data; documentation strings use shorter placeholders.
    leaked = [m for m in matches if "your_key_here" not in m and "placeholder" not in m]
    assert not leaked, f"real-looking API key in source: {leaked!r}"

    # JWT literal (header.payload.signature, three base64 segments).
    jwt_pattern = re.compile(
        r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    )
    jwt_matches = jwt_pattern.findall(app_source)
    assert not jwt_matches, f"JWT literal in source: {jwt_matches!r}"
