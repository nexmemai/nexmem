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
def test_known_leaked_password_is_caught():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import scan_secrets  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    leaked = "***REDACTED_PASSWORD***"
    matched = any(p.regex.search(leaked) for p in scan_secrets.PATTERNS)
    assert matched, "the rotated incident password must be caught by the scanner"


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
