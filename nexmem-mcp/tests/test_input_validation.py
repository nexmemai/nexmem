"""P12-J3 (Block 5): MCP server input validation.

Four tests covering the rejection-and-acceptance contract of
``server.validate_input``. Tested directly (not through the tool
handlers) because the handlers wrap the helper with a uniform
``ValueError → {"error": ..., "code": "INVALID_INPUT"}`` envelope —
once the helper is right, the wrapping is trivial.
"""

from __future__ import annotations

import pytest

from server import (
    INVALID_INPUT_CODE,
    _MAX_LEN_TEXT,
    _invalid_input_response,
    validate_input,
)


# ── 1 ─────────────────────────────────────────────────────────────────────────
def test_empty_content_is_rejected():
    """Empty string and whitespace-only strings both raise. The error
    is wrapped into the standard envelope by the tool handler."""
    for bad in ("", "   ", "\t\n  ", "\n"):
        with pytest.raises(ValueError) as exc:
            validate_input(bad, "text")
        assert "empty or whitespace" in str(exc.value).lower()

    # Envelope contract — the handler returns this exact shape.
    response = _invalid_input_response(ValueError("text cannot be empty or whitespace"))
    assert response == {
        "error": "text cannot be empty or whitespace",
        "code": INVALID_INPUT_CODE,
    }


# ── 2 ─────────────────────────────────────────────────────────────────────────
def test_content_exceeding_max_length_is_rejected():
    """Anything past the per-field cap raises. The default text cap
    is 10 000; anything longer must be rejected, regardless of how
    well-formed the contents are."""
    too_long = "a" * (_MAX_LEN_TEXT + 1)
    with pytest.raises(ValueError) as exc:
        validate_input(too_long, "text", max_len=_MAX_LEN_TEXT)
    msg = str(exc.value).lower()
    assert "exceeds maximum length" in msg
    assert str(_MAX_LEN_TEXT) in msg

    # A value exactly AT the cap is fine — the cap is inclusive.
    at_cap = "b" * _MAX_LEN_TEXT
    assert validate_input(at_cap, "text", max_len=_MAX_LEN_TEXT) == at_cap


# ── 3 ─────────────────────────────────────────────────────────────────────────
def test_null_byte_in_content_is_rejected():
    """Embedded NUL bytes break Postgres ``text`` columns and are a
    cheap fingerprint of malformed clients. They are rejected
    regardless of position in the string."""
    for bad in ("hello\x00world", "\x00at-start", "at-end\x00", "\x00"):
        with pytest.raises(ValueError) as exc:
            validate_input(bad, "text")
        assert "null bytes" in str(exc.value).lower()


# ── 4 ─────────────────────────────────────────────────────────────────────────
def test_valid_input_is_accepted_and_stripped():
    """Well-formed input round-trips with surrounding whitespace
    stripped — so trailing whitespace cannot inflate apparent length
    after the cap check."""
    assert validate_input("hello", "text") == "hello"
    assert validate_input("  hello  ", "text") == "hello"
    assert validate_input("\thello\n", "text") == "hello"
    assert validate_input("contains spaces inside", "text") == "contains spaces inside"

    # Non-string inputs are also rejected (the user-facing tool
    # handlers receive validated types via FastMCP, but defence in
    # depth — direct callers of the helper get the same guarantee).
    for bad in (None, 42, [], {}, b"bytes"):
        with pytest.raises(ValueError) as exc:
            validate_input(bad, "field")  # type: ignore[arg-type]
        assert "must be a string" in str(exc.value).lower()
