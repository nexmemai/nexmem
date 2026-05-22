"""
Brute-force login protection.

Strategy:
- Track failed attempts per email (primary) and per IP (secondary).
- After 5 consecutive failures the key is locked for LOCKOUT_SECONDS.
- Successful login clears the email counter.
- Uses Redis when available; falls back to a thread-safe in-memory dict.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
MAX_ATTEMPTS = 5          # failures before lockout
LOCKOUT_SECONDS = 900     # 15 minutes
WINDOW_SECONDS = 600      # rolling 10-minute window for attempt counting
# ──────────────────────────────────────────────────────────────────────────────


# ── In-memory fallback store ───────────────────────────────────────────────────
_store: dict[str, list[float]] = {}   # key → list of failure timestamps
_lock = threading.Lock()


def _mem_record_failure(key: str) -> int:
    """Record a failure and return the current attempt count within the window."""
    now = time.time()
    cutoff = now - WINDOW_SECONDS
    with _lock:
        timestamps = [t for t in _store.get(key, []) if t > cutoff]
        timestamps.append(now)
        _store[key] = timestamps
        return len(timestamps)


def _mem_is_locked(key: str) -> bool:
    """Return True if the key has hit the attempt limit within the window."""
    now = time.time()
    cutoff = now - WINDOW_SECONDS
    with _lock:
        timestamps = [t for t in _store.get(key, []) if t > cutoff]
        _store[key] = timestamps
        return len(timestamps) >= MAX_ATTEMPTS


def _mem_clear(key: str) -> None:
    with _lock:
        _store.pop(key, None)


# ── Redis helpers ──────────────────────────────────────────────────────────────
def _get_redis():
    """Return a Redis client or None if Redis is not configured."""
    try:
        from app.config import settings
        if not settings.redis_url:
            return None
        import redis as redis_lib
        client = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception:
        return None


def _redis_record_failure(client, key: str) -> int:
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, LOCKOUT_SECONDS)
    results = pipe.execute()
    return results[0]  # new count


def _redis_is_locked(client, key: str) -> bool:
    val = client.get(key)
    return val is not None and int(val) >= MAX_ATTEMPTS


def _redis_clear(client, key: str) -> None:
    client.delete(key)


# ── Public API ─────────────────────────────────────────────────────────────────
def _failure_key(identifier: str) -> str:
    return f"login_fail:{identifier}"


async def check_not_locked(
    request: Request,
    email: str,
) -> None:
    """
    Raise 429 if the email or client IP has too many recent failures.
    Call this BEFORE verifying the password.

    P3-A7: also raises 429 if the account is in escalated lockout
    (threshold failures across multiple IPs in the escalation
    window). Recovery requires either the time-based unlock or an
    operator clearing the in-memory state.
    """
    ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )

    if _account_is_locked(email):
        logger.warning(
            "Login blocked (account escalation): email=%s ip=%s", email, ip
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "This account has been temporarily locked due to suspicious "
                "activity. Reach out to support to unlock."
            ),
            headers={"Retry-After": str(_account_lockout_seconds())},
        )

    email_key = _failure_key(f"email:{email.lower()}")
    ip_key = _failure_key(f"ip:{ip}")

    rc = await asyncio.to_thread(_get_redis)

    for key in (email_key, ip_key):
        locked = (
            await asyncio.to_thread(_redis_is_locked, rc, key)
            if rc
            else _mem_is_locked(key)
        )
        if locked:
            logger.warning("Login blocked (brute-force): key=%s", key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many failed login attempts. "
                    f"Try again in {LOCKOUT_SECONDS // 60} minutes."
                ),
                headers={"Retry-After": str(LOCKOUT_SECONDS)},
            )


async def record_failure(request: Request, email: str) -> None:
    """Increment failure counters for both email and IP.

    P3-A7: also increments the per-account escalation counter and
    records the source IP. When that counter exceeds
    ``settings.account_lockout_escalation_threshold`` across more
    than one IP within the escalation window, the account is locked
    until ``account_lockout_escalation_seconds`` elapses, regardless
    of which IP attempts to log in next.
    """
    ip = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    email_key = _failure_key(f"email:{email.lower()}")
    ip_key = _failure_key(f"ip:{ip}")

    rc = await asyncio.to_thread(_get_redis)

    for key in (email_key, ip_key):
        count = (
            await asyncio.to_thread(_redis_record_failure, rc, key)
            if rc
            else _mem_record_failure(key)
        )
        logger.info("Login failure recorded: key=%s count=%d", key, count)

    # P3-A7: account-level escalation. Aggregates across IPs.
    _record_account_escalation(email, ip)


# ── P3-A7: account-level lockout escalation ──────────────────────────────────
# A single user accumulating many failures across many IPs is the
# signature of distributed credential stuffing rather than a
# forgetful human. The thresholds live in settings so an operator
# can tighten them per-tier without a code change.
_account_failures: dict[str, list[tuple[float, str]]] = {}  # email -> [(ts, ip)]
_account_locks: dict[str, float] = {}  # email -> unlock_at_ts


def _account_window_seconds() -> int:
    from app.config import settings as _settings

    return int(_settings.account_lockout_escalation_window_seconds)


def _account_threshold() -> int:
    from app.config import settings as _settings

    return int(_settings.account_lockout_escalation_threshold)


def _account_lockout_seconds() -> int:
    from app.config import settings as _settings

    return int(_settings.account_lockout_escalation_seconds)


def _record_account_escalation(email: str, ip: str) -> None:
    now = time.time()
    cutoff = now - _account_window_seconds()
    key = email.lower()
    with _lock:
        history = [(t, p) for t, p in _account_failures.get(key, []) if t > cutoff]
        history.append((now, ip))
        _account_failures[key] = history
        unique_ips = {p for _, p in history}
        if (
            len(history) >= _account_threshold()
            and len(unique_ips) > 1
        ):
            _account_locks[key] = now + _account_lockout_seconds()
            logger.warning(
                "account_escalation: locking %s for %ss "
                "(%s failures across %s IPs)",
                key,
                _account_lockout_seconds(),
                len(history),
                len(unique_ips),
            )


def _account_is_locked(email: str) -> bool:
    key = email.lower()
    with _lock:
        unlock_at = _account_locks.get(key)
        if unlock_at is None:
            return False
        if time.time() >= unlock_at:
            _account_locks.pop(key, None)
            return False
        return True


def _reset_account_escalation() -> None:
    """Test + operator helper. Wipes all account-escalation state."""
    with _lock:
        _account_failures.clear()
        _account_locks.clear()


async def clear_failures(email: str) -> None:
    """Clear failure counters after a successful login."""
    email_key = _failure_key(f"email:{email.lower()}")
    rc = await asyncio.to_thread(_get_redis)
    if rc:
        await asyncio.to_thread(_redis_clear, rc, email_key)
    else:
        _mem_clear(email_key)
