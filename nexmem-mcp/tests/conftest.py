"""Test config for the nexmem-mcp tests.

The MCP server is shipped as a flat module (``server.py``) plus a
``tools/`` package directly under ``nexmem-mcp/``. To import it from
tests in ``nexmem-mcp/tests/`` we have to put the parent directory
on ``sys.path`` — the package is not installed via ``pip`` in the
test sandbox.

This conftest is scoped to ``nexmem-mcp/tests/`` only, so the path
adjustment cannot leak into the main backend test suite under
``tests/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MCP_ROOT = Path(__file__).resolve().parent.parent
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))
