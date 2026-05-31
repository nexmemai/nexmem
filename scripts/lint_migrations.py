#!/usr/bin/env python3
"""Static-lint pass for Alembic migration files (P5-C6).

Catches the classes of unsafe migration that ``CONTRIBUTING.md §3``
calls out:

1. **Unconditional ``DELETE FROM`` / ``TRUNCATE``.** A migration
   that wipes a table en masse cannot be re-run safely after
   landing. Migration 007 already did this once; the lint blocks
   anyone from doing it again.
2. **Bare ``DROP TABLE``.** Allowed only when the previous
   migration created the table — but verifying that here is hard,
   so we block all ``DROP TABLE`` and ask the author to add an
   inline ``# lint: drop-table-ok`` opt-out comment.
3. **``op.execute("ALTER TABLE ...")``** without an inline
   acknowledgement. Raw ALTERs can take ``ACCESS EXCLUSIVE`` on
   tables with millions of rows and stall the whole service.
4. **Missing ``downgrade()`` body.** A ``downgrade()`` that just
   ``pass``-es turns CI's round-trip test (P5-C5) into a lie.

This is a heuristic pre-commit / CI gate, not a proof. It looks at
text content of the file and is intentionally conservative — false
positives are tolerated; false negatives are not. The author can
opt out per finding with ``# lint: <token>-ok`` on the offending
line.

Exit code 0 = clean, 1 = findings, 2 = usage error.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


_FORBIDDEN = (
    # (pattern, opt_out_token, message)
    (
        re.compile(r"^\s*op\.execute\s*\(\s*[\"']\s*DELETE\s+FROM", re.I),
        "delete-from-ok",
        "Unconditional 'DELETE FROM' is destructive and not idempotent. "
        "Add a WHERE clause or annotate the line with '# lint: delete-from-ok'.",
    ),
    (
        re.compile(r"^\s*op\.execute\s*\(\s*[\"']\s*TRUNCATE", re.I),
        "truncate-ok",
        "TRUNCATE is destructive. If intentional, annotate with "
        "'# lint: truncate-ok'.",
    ),
    (
        re.compile(r"^\s*op\.drop_table\s*\("),
        "drop-table-ok",
        "Dropping a table with op.drop_table(...) requires explicit "
        "acknowledgement. Add '# lint: drop-table-ok' to the line.",
    ),
    (
        re.compile(r"^\s*op\.execute\s*\(\s*[\"'][^\"']*ALTER\s+TABLE", re.I),
        "raw-alter-ok",
        "Raw ALTER TABLE via op.execute can take ACCESS EXCLUSIVE on a "
        "large table. Use op.alter_column / op.add_column / "
        "op.drop_constraint where possible, or annotate '# lint: raw-alter-ok'.",
    ),
)


def _find_downgrade_body(text: str) -> Tuple[bool, bool]:
    """Return ``(found, non_trivial)``.

    ``found`` = there is a ``def downgrade(`` in the file.
    ``non_trivial`` = the body is not a single ``pass``.
    """
    match = re.search(r"def\s+downgrade\s*\([^)]*\)\s*->\s*None\s*:|def\s+downgrade\s*\([^)]*\)\s*:", text)
    if not match:
        return False, False
    after = text[match.end():]
    # Strip the immediate docstring if any.
    after = re.sub(r'^\s*"""(?:.|\n)*?"""', "", after, count=1)
    body_lines = [
        ln for ln in after.split("\n") if ln.strip() and not ln.strip().startswith("#")
    ][:5]
    if not body_lines:
        return True, False
    if len(body_lines) == 1 and body_lines[0].strip() == "pass":
        return True, False
    return True, True


def lint_file(path: Path) -> List[str]:
    findings: List[str] = []
    text = path.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for pattern, opt_out, message in _FORBIDDEN:
            if pattern.search(line):
                # Allow opt-out via ``# lint: <token>-ok`` on the same line
                # or the line immediately above.
                opt_out_token = f"lint: {opt_out}"
                same_line = opt_out_token in line
                prev_line = (
                    i >= 2 and opt_out_token in lines[i - 2]
                )
                if same_line or prev_line:
                    continue
                findings.append(f"{path}:{i}: {message}")
    found, non_trivial = _find_downgrade_body(text)
    if not found:
        findings.append(f"{path}: missing downgrade() function")
    elif not non_trivial:
        findings.append(
            f"{path}: downgrade() body is empty / pass-only. "
            "Round-trip safety (P5-C5) requires a real downgrade."
        )
    return findings


def main(paths: Iterable[Path]) -> int:
    files = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.glob("*.py")))
        else:
            files.append(p)
    if not files:
        print("usage: lint_migrations.py <files-or-dir>", file=sys.stderr)
        return 2
    findings: List[str] = []
    for path in files:
        # Skip __init__.py and non-migration files.
        if path.name == "__init__.py":
            continue
        findings.extend(lint_file(path))
    if findings:
        for line in findings:
            print(line)
        return 1
    print(f"migration lint: clean ({len(files)} file(s) checked)")
    return 0


if __name__ == "__main__":
    args = [Path(a) for a in sys.argv[1:]]
    if not args:
        args = [Path("alembic/versions")]
    sys.exit(main(args))
