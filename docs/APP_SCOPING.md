# App Scoping in Nexmem

**Status:** authoritative as of Phase 2.
**Last reviewed:** 2026-05-22.

This document records the chosen rule for app scoping after Phase 2.
Every router, service, and test must follow it. Deviations are bugs.

---

## 1. The rule

App scope is **request-scoped**. The caller communicates the target
app on every request via:

* the ``app_id`` field of the request body, when the route accepts a
  body (e.g. ``POST /memory/episode/write``, ``POST /rag/chat``,
  ``POST /agents/{user_id}/semantics``); or
* the ``app_id`` query parameter, when the route is read-only
  (e.g. ``GET /agents/{user_id}/episodes``,
  ``GET /memory/engram/{engram_id}``).

The ``app_id`` is **not** a property of the authenticated ``User``
record. Trying to read ``current_user.app_id`` is an
``AttributeError`` and any code that does so is broken.

When the caller omits ``app_id`` entirely, the request operates over
**every app** owned by the user. This is the behaviour the existing
tests rely on and matches a "user-wide" view (e.g. the dashboard
"all apps" tab).

## 2. What persists alongside data

Each row in a memory table carries an optional ``app_id`` UUID column:

* ``episodic_memory.app_id``
* ``semantic_memory.app_id``
* ``procedural_memory.app_id``
* ``knowledge_nodes.app_id``
* ``knowledge_edges.app_id``
* ``engrams.app_id``

Writes set ``app_id`` from the request payload (if provided) and store
``NULL`` otherwise. Reads filter by ``app_id`` only when the caller
sends it; otherwise they see every row owned by the user.

## 3. What does NOT use app scope

These are **per-user** entities and never carry ``app_id``:

* ``users``
* ``api_keys``
* ``refresh_tokens``
* ``token_usage``

A user's API keys, sessions, and quota counters live above any one app.

## 4. App registration

A user can register multiple apps via ``POST /apps/register``. The
registry is currently encoded in ``api_keys.scopes`` as
``"app:<uuid>"``. This is documented as a temporary shape; a first-class
``apps`` table is tracked under R-203 in ``BACKEND_RISKS.md``.

## 5. Validation

Every route that accepts ``app_id`` (body or query) must:

1. Reject malformed UUIDs with HTTP 400 (``"Invalid app_id format"``).
2. When the caller provides ``app_id`` AND the row's ``user_id`` does
   not match ``current_user.id``, return HTTP 403.
3. Never widen access. Filtering is monotonic: providing ``app_id``
   must only narrow the result set, not widen it.

## 6. Tests pinning the rule

The Phase 2 cross-app isolation suite
(``tests/test_app_scoping.py``) covers:

* same user, two distinct ``app_id`` values, episodes do not bleed
  across them;
* one user cannot read another user's memory even when ``app_id`` is
  known;
* ``GDPR /export`` and ``/all`` honour ``app_id`` if present, and
  otherwise act on every row owned by the user;
* graph path queries reject cross-app traversal when ``app_id`` is
  pinned.

A future change to the scoping rule must update both this document and
that test file.
