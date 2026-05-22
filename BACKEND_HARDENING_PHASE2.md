# BACKEND_HARDENING_PHASE2.md

| Field | Value |
|---|---|
| **Branch** | `backend/hardening-phase2` (stacked on `backend/hardening-private-beta`) |
| **Date** | 2026-05-22 |
| **Author** | Senior backend (Phase 2 hardening) |
| **Phase 1 status** | Complete in PR #1 (still open against `main`). |

---

## Objective

Move the NexMem backend from "credible private beta candidate" to
"safe, testable, production-serious backend" by closing the remaining
**HIGH** risks recorded in `BACKEND_RISKS.md`, in dependency order.

This is *correctness, safety, and reliability* work only. It is not
product, billing, SDK, MCP, or marketing work.

---

## Risk items addressed

Each `P2-Cn` corresponds to a numbered task in the founder brief and
maps onto entries in [`BACKEND_RISKS.md`](./BACKEND_RISKS.md). The
"Source" column lists the risk IDs each task closes or partially
closes.

| Task | Title | Source risk(s) |
|---|---|---|
| P2-C1 | Incident completion: secret-exposure cleanup | R-C1 (ops) |
| P2-C2 | Partial-write transactionality | R-H1 |
| P2-C3 | Fix `current_user.app_id` and tenant/app scoping | R-H2, R-H4, R-H5 (engram `app_id`) |
| P2-C4 | Expand RLS / isolation coverage | R-H7 (users / api_keys / token_usage) |
| P2-C5 | Refresh-token / session revocation | R-H11 |
| P2-C6 | Migration-on-startup race + deploy safety | R-H10 |
| P2-C7 | Cold-start + request-path heavy work audit | R-M2, R-M3 |
| P2-C8 | Observability and failure diagnosis | R-H9 (Sentry sample rates), R-M10 |
| P2-C9 | CI truthfulness and verification hardening | R-C8 (deepen) |
| P2-C10 | Final truth pass on repo status | (docs reconciliation) |

The companion document
[`BACKEND_RISKS.md`](./BACKEND_RISKS.md) is the running ledger; entries
are updated to `Status: ✅ FIXED` as P2 work lands, with a pointer to
the verifying test.

---

## Out of scope (intentionally deferred)

- Billing pipeline (Stripe / Paddle) and customer-facing onboarding UI.
- SDK improvements (`nexmem-py`, `nexmem-js`).
- MCP server polish.
- Marketing site, README hero copy, landing pages.
- Any cosmetic refactor unrelated to backend risk.
- Drop the `_generate_demo_reply` LLM-failure fallback (R-H8) — keep
  in Phase 3 to avoid scope creep; the app-scoping fix in P2-C3 is
  the actually-hot bug in `rag.py`.
- API-key scope enforcement (R-M6).
- Hand-paste SQL file consolidation (R-M4).

---

## Operating principles (carried over from Phase 1)

- **Fail closed.** Insecure defaults must crash at startup, not log.
- **Verify from code.** Each fix has a file:line reference and a test,
  manual repro, or migration verification step.
- **No optimism.** "Should work" is not a fix. Either the test passes
  or the change is marked `unverified`.
- **Atomic commits.** One concern per commit, clearly named
  `P2-Cn <subject>` so the PR is bisectable.

---

## Order of work

The order is not arbitrary. Earlier items unblock later items.

1. **P2-C1** first — the credential incident is open, every following
   commit must be guarded by the secret scanner.
2. **P2-C2** — transactional writes — prerequisite for any honest RLS
   test (a partial write breaks the "no orphan rows" assertion).
3. **P2-C3** — app scope consistency — fixes a latent `AttributeError`
   today. Required for cross-app RLS tests in P2-C4.
4. **P2-C4** — expand RLS coverage. After C3, app scope is consistent;
   RLS predicates can reliably reference it.
5. **P2-C5** — refresh-token revocation. Adds a new table and a
   migration; benefits from the now-correct migration-runner work.
6. **P2-C6** — migration-on-startup race fix.
7. **P2-C7** — cold-start audit. Independent of the above; could land
   anywhere but kept after C6 so we don't reorder migrations.
8. **P2-C8** — observability cleanup.
9. **P2-C9** — CI truthfulness pass. Confirms each new test is
   actually executing.
10. **P2-C10** — truth pass on repo status docs.

---

## Definition of done (Phase 2)

- [x] No exposed secrets in current tree (Phase 1) **and** an
      automated repo-wide secret scanner that fails CI on regression.
- [ ] Multi-step writes are atomic; partial-failure tests prove it.
- [ ] No router or service references `current_user.app_id` against a
      `User` model that lacks that field. App scope flows from the
      request body / API key only, consistently.
- [ ] RLS coverage extends to every user-owned table whose leakage
      would matter (`users`, `api_keys`, `token_usage`); integration
      tests prove cross-tenant isolation.
- [ ] Refresh tokens can be revoked; revoked tokens cannot mint new
      access tokens; logout-all invalidates all active sessions for
      a user.
- [ ] Multi-replica startup cannot race the migration runner.
- [ ] First-call cold-load of ML models is measured, capped, and not
      duplicated across requests.
- [ ] Observability is honest (request_id / user_id / app_id / route
      / status / latency / sample-rate) and PII-redacted.
- [ ] CI runs every relevant integration test on the merge gate; a
      coverage signal is emitted.
- [ ] Status docs honestly reflect the codebase after Phase 2.

---

## Out-of-band actions still required from operators

These cannot be automated and remain open from Phase 1:

| Action | Why | Owner |
|---|---|---|
| Rotate the previously-leaked Supabase database password. | The literal was removed from HEAD in Phase 1, but it remains in `git` history and must be considered compromised. | Founder / DBA |
| Scrub `git` history with `git-filter-repo` or BFG, then force-push. | Same reason as above. | Founder |
| Audit and rotate Supabase `service_role` keys if any are exposed in code, environments, or third-party integrations. | Service role bypasses RLS; rotation is the only safe response if exposure is suspected. | Founder |
| Confirm no `.env*` file is committed in any branch. | Defensive verification. | Founder |
| Notify collaborators to re-clone or hard-reset after the history rewrite. | Force-push invalidates local refs. | Founder |

P2-C1 ships an explicit operator runbook
([`docs/SECRET_INCIDENT_RUNBOOK.md`](./docs/SECRET_INCIDENT_RUNBOOK.md))
covering each of these.
