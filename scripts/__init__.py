"""scripts package marker.

Empty by design: lets pytest import operator tooling (e.g.
``from scripts.nexmem_admin import main``) without requiring the
caller to mutate ``sys.path`` first. Adding this file does not
change the behaviour of any existing standalone script — they
continue to run as ``python scripts/foo.py``.
"""
