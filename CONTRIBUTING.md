# Contributing to Nexmem

This guide is the source of truth for contributing engineers and for
anyone reviewing a PR. Phase 2 hardening introduced these rules; they
apply to every change.

---

## 1. Branching and commits

* Branch off `main`. Use a short, scoped prefix (`fix/`, `chore/`,
  `feat/`, `docs/`).
* Keep commits small and bisectable. The Phase 2 hardening branch
  used `P2-S<step>` prefixes; subsequent waves should use the same
  shape if doing a multi-step pass.
* Squashing on merge is fine. Force-pushing to a shared branch is
  not. Operator-only history rewrites follow `docs/INCIDENT_RUNBOOK.md`.

## 2. Secrets and credentials

* **Never hardcode a credential, anywhere.** Local development reads
  `.env` / `.env.local`; production reads Render env vars. Both are
  excluded by `.gitignore`. Templates live in `.env.example` with
  placeholder values only.
* `scripts/scan_secrets.py` runs in CI on every push and pull
  request. It blocks Postgres URLs with embedded passwords, Supabase
  hostnames, JWT-shaped strings, OpenAI live keys, AWS access keys,
  GitHub PATs, and the specific incident strings from the Phase 1
  credential rotation.
* If you trip the scanner on a legitimate placeholder, add the
  surrounding pattern to `PLACEHOLDER_NEEDLES` in
  `scripts/scan_secrets.py` rather than disabling the scan.

### 2.1 Secret-pattern test fixtures

Test fixtures sometimes need to *look* like a real secret in order to
exercise validation, scanners, or third-party SDK code paths. GitHub
push protection scans the wire format of every push; a contiguous
secret-shaped literal will reject the push even when the file is
clearly a test fixture.

**Rule:** never put a contiguous secret-pattern literal in a test
file. Construct it at runtime from non-contiguous parts.

Wrong:

```python
FAKE_STRIPE_KEY = "sk_live_abcdefghijklmnopqrstuv"
FAKE_OPENAI_KEY = "sk-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
```

Right:

```python
FAKE_STRIPE_KEY = "sk_live_" + "abcdefghijklmnopqrstuv"
FAKE_OPENAI_KEY = "".join(["sk-", "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"])
```

The scanner in `scripts/scan_secrets.py` and downstream push-protection
systems both look for contiguous patterns. Splitting on a `+` or
`"".join([...])` boundary defeats both detectors and keeps the test
fixture readable.

## 3. Migration authoring guidelines

Every Alembic migration runs in production via the advisory-locked
wrapper `scripts/run_with_migrations.sh`. Authors must follow these
rules so a deploy cannot drop user data or take a long lock during
peak traffic.

### 3.1 Required

* Migrations must be **idempotent on retry**. If a migration fails
  half-way, the operator should be able to re-run it without manual
  fix-ups. Use `IF NOT EXISTS` / `IF EXISTS` clauses where Postgres
  supports them.
* Every migration ships with a working `downgrade()`. If a downgrade
  is genuinely impossible (e.g. data lossy), say so in the docstring
  and `pass` in `downgrade()`.
* Use Alembic ops (`op.create_table`, `op.add_column`, …) where
  possible. Drop into raw SQL only for things Alembic cannot express
  (RLS policies, advisory locks, `pgvector` index ops, etc.).
* Index creation on a large table must use `CREATE INDEX
  CONCURRENTLY` and run outside a transaction. Set
  ``op.get_bind().execution_options(isolation_level="AUTOCOMMIT")``
  for the duration.

### 3.2 Forbidden without a justification in the docstring

* Unconditional `DELETE FROM`, `TRUNCATE`, or `DROP TABLE` statements
  on data-bearing tables. Migration `007_standardize_vector_dim.py`
  is a known exception that has already been applied; it should not
  be re-run, and is documented in `BACKEND_RISKS.md` (R-205).
* Column type changes without a fallback. Use `ALTER COLUMN … TYPE
  USING …` only when the cast is total and you have shipped an
  explicit shadow column / backfill / swap pattern.
* Long `ACCESS EXCLUSIVE` locks on production tables. Prefer
  `ALTER TABLE … ADD COLUMN … NULL` (instant), backfill in batches,
  then `SET NOT NULL` once the data is in place.
* Renames of columns or tables in a single migration. Do them as
  add-new + dual-write + backfill + drop-old across at least two
  deploys.

### 3.3 RLS policies

* Every user-scoped table must have RLS enabled and forced. The
  `app.current_user_id` setting carries the identity; see migrations
  `008_enable_memory_rls.py` and `013_extend_rls.py`.
* When adding a new user-scoped table, add a matching RLS migration
  in the same PR. Reviewers should refuse a new table without it.
* Service-role / system inserts (e.g. token usage tracking) must
  call `set_rls_context` on the session before issuing the INSERT,
  otherwise the policy will reject the row.

## 4. Testing

* Every fix needs a test. The test must fail before the fix and pass
  after. CI runs unit tests on every PR; integration tests run with
  real Postgres + Redis service containers.
* Mark tests with one of `unit`, `integration`, `slow`. The default
  pytest invocation deselects `slow` and `integration`; the
  integration job opts back in via `-m integration`.
* Do not add a test that requires network access without marking it
  `slow`. The default unit job runs without internet.

## 5. Async-safety rules

* Heavy synchronous CPU work in an async route handler must go
  through `app/core/concurrency.py::run_bounded(pool, fn, …)`. Direct
  `asyncio.to_thread` or `loop.run_in_executor` calls without a pool
  cap are not allowed in the request path.
* New external service calls (HTTP, LLM) need a timeout and a
  retry/backoff strategy that fails closed if the service is down.

## 6. Logging and observability

* Log lines that may contain user content go through `structlog`
  with the structured fields `request_id`, `user_id`, `app_id`,
  `route`, `method`, `status`, `latency_ms`. PII fields (email,
  passwords, raw tokens, raw API keys) must never appear in a log
  line.
* If you add a new field that could carry user content, update the
  redaction list in `app/middleware/logging.py` and add a test that
  asserts the field is redacted.

## 7. Pull request checklist

Before opening a PR:

- [ ] `pytest -m "not slow and not integration"` is green locally.
- [ ] `python scripts/scan_secrets.py` reports `clean`.
- [ ] If you touched a model, you added or updated an Alembic migration.
- [ ] If you touched auth, RLS, or quota wiring, you added a test that
      fails without your change.
- [ ] You updated `BACKEND_RISKS.md` if your change resolves or moves
      an entry.
- [ ] You did not commit any `.env` file or any binary larger than
      1 MB.
