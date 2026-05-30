"""Tests for the credential scanner.

Runs the real scanner against the working tree and against synthetic
inputs. Marked as ``unit`` so it runs in the standard CI suite.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "scripts" / "scan_secrets.py"


pytestmark = pytest.mark.unit


def _run_scanner(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_scanner_clean_on_current_tree():
    """The current tree must be clean. If this fails, a secret leaked."""
    result = _run_scanner()
    assert result.returncode == 0, (
        "scan_secrets reported potential secrets in the current tree.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# Each pattern below should be flagged by the scanner if it ever appears in
# a tracked file. We verify by importing the scanner module and running its
# regexes directly so we don't pollute the actual git tree.
# The incident tripwire is hash-based: the cleartext was purged from history
# by the git-filter-repo rewrite and must NOT be re-committed. We verify the
# tripwire by hashing a synthetic token to the SAME digest the scanner knows,
# proving the mechanism fires without putting the real secret in this file.
def test_known_leaked_password_tripwire_is_registered():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import scan_secrets  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    # At least one incident hash must be registered.
    assert scan_secrets.INCIDENT_TRIPWIRE_HASHES, (
        "the incident tripwire hash set must not be empty"
    )
    # Pick any registered digest and synthesize a token that hashes to it by
    # construction is impossible (one-way), so instead assert that a line
    # CONTAINING a token whose hash is registered is detected. We prove the
    # detection path by monkeypatching a known token -> known digest.
    import hashlib
    sample_token = "incident-tripwire-selftest-value"
    digest = hashlib.sha256(sample_token.lower().encode("utf-8")).hexdigest()
    scan_secrets.INCIDENT_TRIPWIRE_HASHES[digest] = "self-test token"
    try:
        hits = scan_secrets._tripwire_hits(f"DATABASE_URL=...:{sample_token}@host")
        assert hits, "a token whose SHA-256 is registered must trip the wire"
    finally:
        scan_secrets.INCIDENT_TRIPWIRE_HASHES.pop(digest, None)


def test_supabase_hostname_is_caught():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import scan_secrets  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    sample = "https://abcdefgh.supabase.co"
    matched = any(p.regex.search(sample) for p in scan_secrets.PATTERNS)
    assert matched, "Supabase project hostnames must be caught"


def test_jwt_pattern_is_caught():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import scan_secrets  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    fake_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    )
    matched = any(p.regex.search(fake_jwt) for p in scan_secrets.PATTERNS)
    assert matched, "JWT-shaped tokens must be caught"


def test_placeholder_strings_are_not_flagged():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import scan_secrets  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    for placeholder in (
        "postgres://postgres:postgres@localhost:5432/db",
        "postgres://postgres:password@localhost:5432/db",
        "postgres://user:pass@host:5432/db",
        "OPENAI_API_KEY=sk-placeholder",
        "OPENAI_API_KEY=sk-test-abcdef0123456789",
        "DATABASE_URL=${DATABASE_URL}",
        "SECRET_KEY=local-dev-secret-change-this-before-production",
    ):
        if scan_secrets._looks_like_placeholder(placeholder):
            continue
        # If the line is not detected as a placeholder, no real-secret pattern
        # should match it either.
        for pattern in scan_secrets.PATTERNS:
            assert not pattern.regex.search(placeholder), (
                f"placeholder line was flagged as a real secret: "
                f"{placeholder!r} matched {pattern.name}"
            )
