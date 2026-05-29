"""Refresh-token revocation: unit-level guards (R-H11).

These tests use no DB. They cover:
  - create_refresh_token() returns (token, jti, expires_at).
  - The encoded JWT carries a `jti` claim equal to the returned id.
  - Two consecutive calls produce distinct jti values.
  - The `type` claim is always 'refresh'.

Live-DB rotation / logout / logout-all tests live under
tests/integration/test_refresh_token_revocation.py.
"""

from __future__ import annotations

import re
import uuid

from jose import jwt

from app.config import settings
from app.core.security import ALGORITHM, create_refresh_token


def test_create_refresh_token_returns_tuple_with_jti() -> None:
    token, jti, exp = create_refresh_token(subject="11111111-1111-1111-1111-111111111111")
    assert isinstance(token, str) and len(token) > 50
    # jti is a UUID string.
    uuid.UUID(jti)
    # The jti embedded in the JWT must match.
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    assert payload["jti"] == jti
    assert payload["type"] == "refresh"
    assert payload["sub"] == "11111111-1111-1111-1111-111111111111"
    # exp matches the JWT's exp claim within 1 second.
    assert abs(payload["exp"] - int(exp.timestamp())) <= 1


def test_two_calls_produce_distinct_jtis() -> None:
    _, jti_a, _ = create_refresh_token(subject="x")
    _, jti_b, _ = create_refresh_token(subject="x")
    assert jti_a != jti_b


def test_explicit_jti_is_honoured() -> None:
    explicit = "12345678-1234-1234-1234-123456789012"
    _, jti, _ = create_refresh_token(subject="x", jti=explicit)
    assert jti == explicit


def test_auth_router_uses_tuple_return() -> None:
    """The auth router must consume the (token, jti, expires_at) tuple
    rather than the legacy single-string return form. If a future patch
    reverts to `token = create_refresh_token(...)`, this test fails.

    Tuple destructuring (`refresh, jti, exp = create_refresh_token(...)`)
    is allowed and is the intended pattern.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath(
        "app", "routers", "auth.py"
    ).read_text()
    # Match a single-identifier assignment: `<word> = create_refresh_token(`
    # at start of line. Tuple destructuring has commas before `=` and is
    # NOT matched.
    bad = re.findall(
        r"^\s*\w+\s*=\s*create_refresh_token\(",
        src,
        flags=re.MULTILINE,
    )
    assert not bad, (
        "auth.py contains a single-assignment call to create_refresh_token; "
        "the function returns (token, jti, expires_at). Update the call site."
    )
