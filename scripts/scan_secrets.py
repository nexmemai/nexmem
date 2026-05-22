#!/usr/bin/env python3
"""Scan the working tree for credential leaks.

Run via:
    python scripts/scan_secrets.py [--ci]

Exits with status 1 if any pattern matches a tracked file. The script is
intentionally allowlist-based for "looks like a real value" checks so that
template strings such as `sk-...` and `postgres:postgres@localhost` do not
trip it.

Patterns covered:
    * Postgres URLs that contain a non-trivial password (so
      `postgres://user@host` and `postgres://postgres:postgres@localhost`
      are both ignored).
    * Supabase project hostnames (`*.supabase.co`).
    * The known leaked Supabase project ref / password from the Phase 1
      incident, kept as an explicit tripwire so the same secret cannot
      re-enter HEAD.
    * Long base64-like JWTs (3 dot-separated chunks of base64url).
    * GitHub Personal Access Tokens (`ghp_`, `ghs_`, `gho_` etc.).
    * AWS access keys (AKIA / ASIA prefix + 16 chars).
    * OpenAI live keys (`sk-` + 40+ chars of base64; `sk-...`,
      `sk-test-*` and `sk-placeholder` are ignored).
    * Bearer tokens that look real (40+ chars after `Bearer `).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern[str]
    description: str


# Substrings that mean "this is a placeholder / template, not a real secret"
PLACEHOLDER_NEEDLES = (
    "your-",
    "yourdomain",
    "changeme",
    "example.com",
    "placeholder",
    "<replace",
    "REPLACE_ME",
    "sk-placeholder",
    "sk-test-",
    "sk-...",
    "postgres:postgres@",       # local dev / docker-compose default
    "postgres:password@",       # docs / .env.example placeholder
    "test-secret",
    "local-dev-secret",
    "user:pass@",               # docs placeholder
    "user:password@",
    "USER:PASSWORD",
    "{password}",
    "{user}:{password}",
    "$DATABASE_URL",
    "${DATABASE_URL}",
)


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "incident_known_leaked_supabase_password",
        re.compile(r"***REDACTED_PASSWORD***", re.IGNORECASE),
        "the rotated Supabase password from the Phase 1 credential incident",
    ),
    Pattern(
        "incident_known_leaked_supabase_project_ref",
        re.compile(r"***REDACTED_PROJECT_ID***", re.IGNORECASE),
        "the Supabase project ref tied to the rotated credential",
    ),
    Pattern(
        "supabase_hostname",
        re.compile(r"\b[a-z0-9-]{8,}\.supabase\.co\b", re.IGNORECASE),
        "Supabase project hostname",
    ),
    Pattern(
        "postgres_url_with_password",
        re.compile(
            r"postgres(?:ql)?(?:\+\w+)?://[^\s:/@]+:[^\s/@]{4,}@[^\s/]+",
            re.IGNORECASE,
        ),
        "Postgres connection string with embedded password",
    ),
    Pattern(
        "openai_live_key",
        re.compile(r"\bsk-[A-Za-z0-9_\-]{30,}\b"),
        "OpenAI live API key",
    ),
    Pattern(
        "aws_access_key",
        re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
        "AWS access key id",
    ),
    Pattern(
        "github_pat",
        re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
        "GitHub personal access token",
    ),
    Pattern(
        "jwt_like",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"),
        "JWT-like token (Supabase service-role keys, etc.)",
    ),
    Pattern(
        "bearer_token_long",
        re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{40,}", re.IGNORECASE),
        "Bearer token that looks real",
    ),
)


def _looks_like_placeholder(line: str) -> bool:
    lowered = line.lower()
    return any(needle.lower() in lowered for needle in PLACEHOLDER_NEEDLES)


def _git_tracked_files() -> list[Path]:
    """Return repo-relative paths of every git-tracked file."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=REPO_ROOT, text=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        sys.stderr.write(f"failed to list git files: {exc}\n")
        sys.exit(2)
    paths = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        paths.append(REPO_ROOT / line)
    return paths


# Files / extensions we explicitly skip — binary blobs, lockfiles, the
# scanner's own self-tests, and the risk register that documents the
# incident strings.
_SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".pdf",
    ".woff", ".woff2", ".ttf", ".ico", ".lock", ".lockb",
)
_SKIP_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}
_SKIP_PATH_PREFIXES = (
    "scripts/scan_secrets.py",
    "tests/test_secret_scan.py",
    "BACKEND_RISKS.md",
    "BACKEND_HARDENING_PHASE2.md",
    "docs/INCIDENT_RUNBOOK.md",
)


def _should_scan(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if any(rel.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
        return False
    if path.name in _SKIP_NAMES:
        return False
    if path.suffix.lower() in _SKIP_SUFFIXES:
        return False
    if not path.exists() or not path.is_file():
        return False
    if path.stat().st_size > 2_000_000:  # 2 MB cap; secrets fit easily
        return False
    return True


def _scan_file(path: Path) -> Iterable[tuple[Pattern, int, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _looks_like_placeholder(line):
            continue
        for pattern in PATTERNS:
            if pattern.regex.search(line):
                yield pattern, lineno, line.rstrip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ci",
        action="store_true",
        help="emit GitHub Actions-style workflow annotations on hits",
    )
    args = parser.parse_args(argv)

    findings: list[tuple[Path, Pattern, int, str]] = []
    for path in _git_tracked_files():
        if not _should_scan(path):
            continue
        for pattern, lineno, line in _scan_file(path):
            findings.append((path, pattern, lineno, line))

    if not findings:
        print("scan_secrets: clean (no matches in tracked files)")
        return 0

    print("scan_secrets: FOUND POTENTIAL SECRETS")
    for path, pattern, lineno, line in findings:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if args.ci:
            print(f"::error file={rel},line={lineno}::{pattern.name}: {pattern.description}")
        else:
            print(f"  {rel}:{lineno}  [{pattern.name}]")
            print(f"      {line[:200]}")
    print()
    print(f"Total findings: {len(findings)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
