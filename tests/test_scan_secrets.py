"""Tests for `scripts/scan_secrets.py`.

Two layers:

1. **Behaviour** — feed a synthetic temp directory that contains each
   pattern type and assert the scanner detects it. Also feed a known-
   clean fixture and assert no hits.
2. **Repo guard** — run the scanner across the live repo tree and
   assert it returns zero hits. This is the regression gate that
   replaces (and supersedes) the narrow `test_alembic_env.py`
   whole-repo scan from Phase 1.

The scanner is intentionally tuned for high precision over recall;
any future tuning should add a positive case here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "scan_secrets.py"


def _load_scanner():
    import sys

    spec = importlib.util.spec_from_file_location("scan_secrets", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can introspect cls.__module__.
    sys.modules["scan_secrets"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def scanner():
    return _load_scanner()


# ── 1. Behavioural cases ────────────────────────────────────────────────────

# Fixtures: a single file in a temp directory containing the suspicious literal.
_POSITIVE_CASES = [
    (
        "supabase-password-literal",
        "config.py",
        'PASSWORD = "***REDACTED_PASSWORD***"\n',
    ),
    (
        "supabase-project-ref",
        "render.yaml",
        "host: db.***REDACTED_PROJECT_ID***.supabase.co\n",
    ),
    (
        "aws-access-key-id",
        "settings.py",
        'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n',
    ),
    (
        "stripe-live-secret-key",
        "billing.py",
        # Build at runtime so no source literal triggers GitHub's
        # secret-scan / push-protection. The string only exists in
        # memory during the test.
        'stripe.api_key = "' + 'sk_' + 'live_' + 'A' * 24 + '"\n',
    ),
    (
        "openai-api-key",
        "llm.py",
        'OPENAI = "' + 'sk-' + "A" * 48 + '"\n',
    ),
    (
        "slack-token",
        "notify.ts",
        'const t = "' + 'xoxb-' + '1234567890-abcdefghij' + '";\n',
    ),
    (
        "google-api-key",
        "maps.js",
        'const key = "' + 'AIzaSy' + "B" * 33 + '";\n',
    ),
    (
        "github-personal-access-token",
        "deploy.sh",
        'GH_TOKEN=' + 'ghp_' + "x" * 36 + '\n',
    ),
    (
        "rsa-private-key-block",
        "deploy_key.pem",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK...\n",
    ),
    (
        "db-url-with-password",
        "secrets.env",
        "DATABASE_URL=postgresql://admin:hunter2@db.example.com:5432/main\n",
    ),
]


@pytest.mark.parametrize("expected_pattern, filename, content", _POSITIVE_CASES)
def test_scanner_detects(expected_pattern, filename, content, tmp_path, scanner):
    (tmp_path / filename).write_text(content)
    hits = scanner.scan_repo(tmp_path)
    pattern_names = {h.pattern for h in hits}
    assert expected_pattern in pattern_names, (
        f"expected the {expected_pattern!r} pattern to fire on {filename}, "
        f"got {pattern_names}"
    )


@pytest.mark.parametrize(
    "filename, content",
    [
        # Loopback host is allowed.
        ("env.example", "DATABASE_URL=postgresql://u:p@localhost:5432/db\n"),
        # Placeholder token in the password slot is allowed.
        (
            "pytest.ini",
            "DATABASE_URL=postgresql+asyncpg://placeholder:placeholder@127.0.0.1:1/x\n",
        ),
        # Plain prose without secrets.
        ("README.md", "We use AWS S3 and Stripe and OpenAI.\n"),
        # The 'sk-' prefix on its own is fine; needs entropy/length match.
        ("docs.md", "Set OPENAI_API_KEY to your real key (sk-…).\n"),
    ],
)
def test_scanner_does_not_false_positive(filename, content, tmp_path, scanner):
    (tmp_path / filename).write_text(content)
    hits = scanner.scan_repo(tmp_path)
    assert hits == [], f"unexpected hits: {[h.render() for h in hits]}"


# ── 2. Repo-wide guard ─────────────────────────────────────────────────────


def test_repo_is_clean(scanner) -> None:
    """The current tree must contain zero secrets according to the scanner.

    This subsumes the Phase-1 whole-repo guard.
    """
    hits = scanner.scan_repo(REPO_ROOT)
    rendered = [h.render() for h in hits]
    assert not hits, "secret-scanner found leaks in the working tree:\n" + "\n".join(rendered)
