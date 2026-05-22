"""Observability hygiene tests (P2-C8 / R-H9 / R-M10).

Covers:
  - log_redactor.redact_sensitive scrubs known-sensitive keys.
  - The structlog pipeline configured by configure_logging() includes
    the redactor before JSONRenderer.
  - Settings.sentry_traces_sample_rate / sentry_profiles_sample_rate
    have conservative defaults (R-H9) and can be overridden via env.
  - HTTP middleware emits the expected per-request log fields when
    driven through the actual TestClient.
"""

from __future__ import annotations

import json
import logging

import pytest


# ── Redactor ────────────────────────────────────────────────────────────────


def test_redactor_scrubs_known_keys() -> None:
    from app.core.log_redactor import redact_sensitive

    out = redact_sensitive(
        None,
        "info",
        {
            "event": "auth_login",
            "password": "hunter2",
            "hashed_password": "bcrypt:abc",
            "access_token": "ey…",
            "refresh_token": "ey…",
            "api_key": "mem_xyz",
            "key_hash": "abcd1234",
            "secret_key": "shhh",
            "Authorization": "Bearer ey…",
            "user_id": "00000000-0000-0000-0000-000000000001",
            "method": "POST",
        },
    )

    for sensitive in (
        "password",
        "hashed_password",
        "access_token",
        "refresh_token",
        "api_key",
        "key_hash",
        "secret_key",
        "Authorization",
    ):
        assert out[sensitive] == "<redacted>", (
            f"{sensitive} was not redacted: {out[sensitive]!r}"
        )

    # Non-sensitive fields pass through untouched.
    assert out["user_id"] == "00000000-0000-0000-0000-000000000001"
    assert out["method"] == "POST"
    assert out["event"] == "auth_login"


def test_redactor_walks_nested_dicts() -> None:
    from app.core.log_redactor import redact_sensitive

    out = redact_sensitive(
        None,
        "info",
        {
            "event": "x",
            "headers": {"Authorization": "Bearer s3cret", "X-Request-ID": "abc"},
        },
    )
    assert out["headers"]["Authorization"] == "<redacted>"
    assert out["headers"]["X-Request-ID"] == "abc"


def test_redactor_does_not_match_innocent_substrings() -> None:
    """A 'token_count' field is not a token; we accept the substring match
    rule and document this trade-off in log_redactor.py.

    The current rule DOES redact 'token_count' because 'token' is in the
    redact list. This is a deliberate safety bias: we'd rather over-redact
    a metric than leak a real bearer.

    This test exists so the trade-off is explicit; if a future change
    flips it, the test must change with it.
    """
    from app.core.log_redactor import redact_sensitive

    out = redact_sensitive(None, "info", {"prompt_tokens": 12, "completion_tokens": 30})
    # Both are redacted — this is the over-redact bias documented above.
    assert out["prompt_tokens"] == "<redacted>"
    assert out["completion_tokens"] == "<redacted>"


# ── configure_logging pipeline shape ───────────────────────────────────────


def test_configure_logging_pipeline_includes_redactor() -> None:
    """Verify the redactor sits in the structlog processor chain
    immediately before JSONRenderer.
    """
    import structlog

    from app.core.logging import configure_logging
    from app.core.log_redactor import redact_sensitive

    configure_logging()
    config = structlog.get_config()
    processors = config["processors"]
    names = [
        p.__name__ if hasattr(p, "__name__") else type(p).__name__ for p in processors
    ]
    assert "redact_sensitive" in names
    redactor_idx = names.index("redact_sensitive")
    json_idx = names.index("JSONRenderer")
    assert redactor_idx < json_idx, (
        "redactor must run BEFORE the JSON renderer; otherwise sensitive "
        "values reach the serialised string."
    )


# ── Sentry sample-rate defaults ────────────────────────────────────────────


def test_sentry_sample_rates_have_conservative_defaults() -> None:
    from app.config import Settings

    s = Settings(
        environment="development",
        demo_mode=True,
        secret_key="x" * 64,
        database_url="postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/x",
        openai_api_key="sk-test",
    )
    assert s.sentry_traces_sample_rate == 0.1, (
        "Phase-1 had 1.0 (cost bomb at any traffic); private beta default "
        "must be ≤ 0.1. R-H9."
    )
    assert s.sentry_profiles_sample_rate == 0.0


def test_sentry_sample_rates_overridable_via_env(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("SENTRY_PROFILES_SAMPLE_RATE", "0.05")
    s = Settings(
        environment="development",
        demo_mode=True,
        secret_key="x" * 64,
        database_url="postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/x",
        openai_api_key="sk-test",
    )
    assert s.sentry_traces_sample_rate == 0.5
    assert s.sentry_profiles_sample_rate == 0.05


# ── HTTP middleware shape ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_middleware_logs_request_id_and_route(client) -> None:
    """Drive the demo-mode app and capture the structlog output."""
    import structlog

    captured: list[dict] = []

    def _capture(_logger, _method_name, event_dict):
        captured.append(dict(event_dict))
        return event_dict

    # Insert a capturing processor just before redaction so we see the
    # full event shape (the redactor is what we will assert about
    # separately).
    cfg = structlog.get_config()
    new_processors = list(cfg["processors"])
    new_processors.insert(-2, _capture)
    structlog.configure(processors=new_processors)
    try:
        r = await client.get("/health/live")
        assert r.status_code == 200
        # The request_id header is present.
        assert "X-Request-ID" in r.headers
        request_id = r.headers["X-Request-ID"]

        # The captured logs include an http_request event with the right
        # shape and request_id.
        events = [e for e in captured if e.get("event") == "http_request"]
        assert events, f"no http_request event captured; saw {captured}"
        e = events[-1]
        assert e["request_id"] == request_id
        assert e["method"] == "GET"
        assert e["path"] == "/health/live"
        assert e["status_code"] == 200
        assert "latency_ms" in e and isinstance(e["latency_ms"], (int, float))
    finally:
        structlog.configure(processors=cfg["processors"])
