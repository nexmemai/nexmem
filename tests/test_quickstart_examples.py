"""Pin the SDK quickstart examples to the documented shape (Block 9).

These tests are docs-shape regressions, not runtime checks. They run
without starting the backend and without installing the JS toolchain.
They intentionally do NOT exercise the example scripts end-to-end —
that is a manual local task documented in ``examples/README.md``.

What they pin:

1. Both example files exist alongside the index README.
2. Both examples reference the canonical ``nxm_`` API key prefix and
   never the legacy ``mem_`` / ``sk_live_`` shapes (Rule #15 in
   ``KIRO_SESSION_BOOTSTRAP.md``).
3. Both examples target the local dev backend at ``localhost:8000``.
4. Neither example hard-codes a real-looking API key literal — keys
   must be minted at runtime via the API.
5. Both SDK READMEs link to the matching example file so a new
   developer can discover them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
PY_EXAMPLE = EXAMPLES_DIR / "python_quickstart.py"
JS_EXAMPLE = EXAMPLES_DIR / "javascript_quickstart.mjs"
EXAMPLES_README = EXAMPLES_DIR / "README.md"
PY_SDK_README = REPO_ROOT / "nexmem-py" / "README.md"
JS_SDK_README = REPO_ROOT / "nexmem-js" / "README.md"


def test_quickstart_files_exist():
    assert PY_EXAMPLE.is_file(), "examples/python_quickstart.py is missing"
    assert JS_EXAMPLE.is_file(), "examples/javascript_quickstart.mjs is missing"
    assert EXAMPLES_README.is_file(), "examples/README.md is missing"


def test_examples_use_nxm_prefix_not_legacy_prefixes():
    """Examples must reference the canonical ``nxm_`` prefix and avoid
    every legacy shape (``mem_``, ``sk_live_``, ``"sk-"``)."""
    py_text = PY_EXAMPLE.read_text()
    js_text = JS_EXAMPLE.read_text()

    assert "nxm_" in py_text, "python_quickstart.py must reference nxm_ prefix"
    assert "nxm_" in js_text, "javascript_quickstart.mjs must reference nxm_ prefix"

    legacy_prefixes = ("mem_", "sk_live_")
    for legacy in legacy_prefixes:
        assert legacy not in py_text, (
            f"legacy prefix {legacy!r} found in python_quickstart.py"
        )
        assert legacy not in js_text, (
            f"legacy prefix {legacy!r} found in javascript_quickstart.mjs"
        )


def test_examples_target_local_dev_server():
    """Local quickstarts must point at ``http://localhost:8000``."""
    py_text = PY_EXAMPLE.read_text()
    js_text = JS_EXAMPLE.read_text()

    assert "http://localhost:8000" in py_text, (
        "python_quickstart.py must default to http://localhost:8000"
    )
    assert "http://localhost:8000" in js_text, (
        "javascript_quickstart.mjs must default to http://localhost:8000"
    )


def test_examples_have_no_hardcoded_real_looking_api_keys():
    """No copy-pasted ``nxm_<long>`` literal can appear in source.

    The quickstarts must mint their key at runtime via the API. The
    only ``nxm_`` occurrences in source should be the four-character
    prefix itself (used in string-concatenation tokens or in literal
    log messages like ``prefix nxm_``). Any quoted ``nxm_`` followed
    by a long random-looking suffix is a smell.
    """
    api_key_literal = re.compile(r"['\"]nxm_[A-Za-z0-9_\-]{8,}['\"]")
    for path in (PY_EXAMPLE, JS_EXAMPLE):
        text = path.read_text()
        match = api_key_literal.search(text)
        assert match is None, (
            f"{path.name} contains a literal API-key-shaped string "
            f"{match.group(0)!r}; mint via the API instead"
        )


@pytest.mark.parametrize(
    "readme_path, example_relpath",
    [
        (PY_SDK_README, "examples/python_quickstart.py"),
        (JS_SDK_README, "examples/javascript_quickstart.mjs"),
    ],
)
def test_sdk_readmes_link_to_matching_example(readme_path, example_relpath):
    """Each SDK README must link to its matching example for discoverability."""
    text = readme_path.read_text()
    assert example_relpath in text, (
        f"{readme_path.relative_to(REPO_ROOT)} does not reference "
        f"{example_relpath!r}"
    )
