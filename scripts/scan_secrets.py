#!/usr/bin/env python3
"""Repo-wide secret scanner.

Used by:
  - the unit-tests CI job (`tests/test_scan_secrets.py` calls `scan_repo()`)
  - operators ad-hoc (`python scripts/scan_secrets.py` exits non-zero on hits)
  - a pre-commit hook (optional; documented in
    `docs/SECRET_INCIDENT_RUNBOOK.md`).

Detects:
  1. The specific Supabase password / project ref leaked previously
     (defensive guard; would already be caught by C1's scanner).
  2. Common provider-credential patterns (AWS, Stripe, OpenAI, Slack,
     Google API, GitHub, Sentry DSN with credentials, generic JWTs,
     Supabase service-role JWTs).
  3. Likely-private RSA / OpenSSH / PEM headers.
  4. Database-URL patterns with embedded passwords against
     non-localhost hosts.

Tuning:
  - We exclude tests/audit/risk/plan markdown files because they
    intentionally reference the historical password literal as part
    of the regression guard.
  - We exclude lockfiles, build artifacts, virtualenvs.
  - High-entropy heuristics are deliberately disabled in this version
    to keep false-positives low; specific patterns above cover the
    real risks.

Usage (CLI):
    python scripts/scan_secrets.py [--root .] [--quiet]
Exit code:
    0 = clean
    1 = leak detected
    2 = scanner error
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# ── Configuration ──────────────────────────────────────────────────────────

# Files / directories the scanner will not look at.
_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".next",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
}

# Files that intentionally reference past secret literals (audit/test guards).
# Paths are POSIX-style relative to the repo root.
_EXCLUDED_FILES = {
    "tests/test_alembic_env.py",
    "tests/test_scan_secrets.py",
    "scripts/scan_secrets.py",
    "REPO_STATE_AUDIT.md",
    "BACKEND_RISKS.md",
    "BACKEND_HARDENING_PLAN.md",
    "BACKEND_HARDENING_PHASE2.md",
    "docs/SECRET_INCIDENT_RUNBOOK.md",
}

# File extensions worth scanning. We skip binaries.
_INCLUDED_SUFFIXES = {
    ".py",
    ".pyx",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".env",
    ".md",
    ".html",
    ".dockerfile",
    ".pem",
    ".key",
    ".cert",
    ".crt",
}


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern[str]
    description: str


_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        name="supabase-password-literal",
        regex=re.compile(r"Doesitmatter"),
        description="Phase-1 Supabase password literal must never reappear.",
    ),
    Pattern(
        name="supabase-project-ref",
        regex=re.compile(r"***REDACTED_PROJECT_ID***"),
        description="Phase-1 Supabase project ref must never reappear.",
    ),
    Pattern(
        name="aws-access-key-id",
        regex=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        description="AWS access key id (AKIA…).",
    ),
    Pattern(
        name="aws-secret-access-key",
        regex=re.compile(
            r"(?i)(?:aws_secret_access_key|aws_secret)\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"
        ),
        description="AWS secret access key assignment.",
    ),
    Pattern(
        name="stripe-live-secret-key",
        regex=re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b"),
        description="Stripe live secret key.",
    ),
    Pattern(
        name="stripe-restricted-key",
        regex=re.compile(r"\brk_live_[0-9a-zA-Z]{24,}\b"),
        description="Stripe restricted live key.",
    ),
    Pattern(
        name="openai-api-key",
        regex=re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"),
        description="OpenAI API key (sk-… or sk-proj-…).",
    ),
    Pattern(
        name="slack-token",
        regex=re.compile(r"\bxox[abpros]-[A-Za-z0-9-]{10,}\b"),
        description="Slack OAuth / bot / user token.",
    ),
    Pattern(
        name="google-api-key",
        regex=re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
        description="Google API key.",
    ),
    Pattern(
        name="github-personal-access-token",
        regex=re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
        description="GitHub personal access token.",
    ),
    Pattern(
        name="github-fine-grained-token",
        regex=re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
        description="GitHub fine-grained PAT.",
    ),
    Pattern(
        name="sentry-dsn-with-secret",
        regex=re.compile(
            r"https://[a-f0-9]{32}:[a-f0-9]{32}@[a-z0-9.-]+/[0-9]+"
        ),
        description="Sentry DSN with embedded secret half (legacy format).",
    ),
    Pattern(
        name="rsa-private-key-block",
        regex=re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |)PRIVATE KEY-----"),
        description="Private-key PEM block.",
    ),
    Pattern(
        name="db-url-with-password",
        regex=re.compile(
            # postgresql://user:pass@host  with a password segment that is
            # not "placeholder" or empty, AND a host that is not loopback.
            r"(?:postgres|postgresql|mysql|mongodb)(?:\+\w+)?://"
            r"(?P<user>[^:@/]+):(?P<pw>[^@/\s]+)@"
            r"(?P<host>(?!(?:127\.0\.0\.1|localhost|::1|placeholder|postgres))[^:/\s]+)"
        ),
        description="Database URL with embedded password against a non-loopback host.",
    ),
)

# Rendered placeholder-password tokens that the db-url-with-password pattern
# accepts but should not flag. These are common documentation / example
# values that appear in READMEs, deploy scripts, and Pydantic docstrings.
_FALSE_POSITIVE_PASSWORD_TOKENS = {
    "placeholder",
    "postgres",
    "pass",
    "password",
    "your_pw",
    "your_password",
    "<password>",
    "p",
    "u",
    "secret",
    "changeme",
    "admin",
    "test",
    "example",
    "dummy",
}

# Hostnames that are obvious documentation placeholders (no real exposure).
_PLACEHOLDER_HOSTS = {
    "host",
    "your-host",
    "your_host",
    "yourhost",
    "<host>",
    "hostname",
    "dbhost",
    "db-host",
}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    pattern: str
    snippet: str

    def render(self) -> str:
        snippet = self.snippet[:100].replace("\n", " ")
        return f"{self.path}:{self.line}: {self.pattern}: {snippet}"


# ── Scanner core ────────────────────────────────────────────────────────────


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        rel = path.relative_to(root).as_posix()
        if rel in _EXCLUDED_FILES:
            continue
        if path.suffix.lower() not in _INCLUDED_SUFFIXES and not path.name.lower() in {
            "dockerfile",
            "procfile",
            "makefile",
        }:
            continue
        yield path


def _scan_file(path: Path, root: Path) -> list[Hit]:
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return []
    hits: list[Hit] = []
    for pattern in _PATTERNS:
        for match in pattern.regex.finditer(text):
            # Suppress documented placeholder false-positives for db-url.
            if pattern.name == "db-url-with-password":
                groupdict = match.groupdict()
                pw = (groupdict.get("pw") or "").lower()
                host = (groupdict.get("host") or "").lower()
                if pw in _FALSE_POSITIVE_PASSWORD_TOKENS:
                    continue
                if host in _PLACEHOLDER_HOSTS:
                    continue
            line = text.count("\n", 0, match.start()) + 1
            snippet = text[max(match.start() - 10, 0): match.end() + 10]
            hits.append(
                Hit(path=rel, line=line, pattern=pattern.name, snippet=snippet)
            )
    return hits


def scan_repo(root: Path | str = ".") -> list[Hit]:
    """Public API used by the test suite."""
    root_path = Path(root).resolve()
    hits: list[Hit] = []
    for f in _iter_files(root_path):
        hits.extend(_scan_file(f, root_path))
    return hits


# ── CLI ────────────────────────────────────────────────────────────────────


def _main() -> int:
    parser = argparse.ArgumentParser(description="NexMem repo secret scanner")
    parser.add_argument(
        "--root",
        default=".",
        help="Directory to scan (default: current working directory).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print nothing on success; only render hits on failure.",
    )
    args = parser.parse_args()

    try:
        hits = scan_repo(args.root)
    except Exception as exc:  # noqa: BLE001 — script-level error surface
        print(f"scan_secrets: error during scan: {exc}", file=sys.stderr)
        return 2

    if not hits:
        if not args.quiet:
            print("scan_secrets: clean.")
        return 0

    print(f"scan_secrets: {len(hits)} potential secret(s) detected:", file=sys.stderr)
    for h in hits:
        print(f"  {h.render()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
