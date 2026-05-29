"""structlog processor that scrubs known-sensitive keys before emission.

Used to keep credentials and bearer-style secrets out of the JSON
log stream that ships to Datadog / ELK / Loki / Sentry breadcrumbs.

Redacted keys (case-insensitive substring match against the event-
dict key name):

    password, hashed_password, refresh_token, access_token, api_key,
    key_hash, authorization, secret_key, secret, token, bearer

When a value is replaced, it is replaced with the literal string
``"<redacted>"`` so engineers reading logs can still see the field
existed without seeing its content.

The processor is conservative: it only inspects top-level keys of
the event dict. Nested dicts inside ``event_dict`` (e.g. structured
exception payloads) are walked one level deep.
"""

from __future__ import annotations

from typing import Any, Dict


_REDACT_TOKENS: tuple[str, ...] = (
    "password",
    "hashed_password",
    "refresh_token",
    "access_token",
    "api_key",
    "key_hash",
    "authorization",
    "secret_key",
    "secret",
    "bearer",
    "token",  # last so 'csrf_token' etc. still match
)

_REDACTED_VALUE = "<redacted>"


def _is_sensitive(key: str) -> bool:
    lower = key.lower()
    return any(needle in lower for needle in _REDACT_TOKENS)


def _redact_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if _is_sensitive(k):
            out[k] = _REDACTED_VALUE
        elif isinstance(v, dict):
            out[k] = _redact_dict(v)
        else:
            out[k] = v
    return out


def redact_sensitive(_logger, _method_name, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """structlog processor signature: (logger, method_name, event_dict)."""
    return _redact_dict(event_dict)
