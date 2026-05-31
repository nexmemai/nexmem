# Data Retention Policy

Last updated: 2026-05-23 (Block 7 / P10-H3).

This document describes Nexmem's data retention policy: which
tables get pruned, on what schedule, and how to override the
defaults per deployment.

## Policy at a glance

| Memory type | Default retention | Config key | Env var | "0 means keep forever"? |
|---|---|---|---|---|
| Episodic | 365 days | `retention_episodic_days` | `RETENTION_EPISODIC_DAYS` | yes |
| Semantic | Forever (default 0) | `retention_semantic_days` | `RETENTION_SEMANTIC_DAYS` | yes |
| Engrams | Forever (default 0) | `retention_engram_days` | `RETENTION_ENGRAM_DAYS` | yes |
| Audit logs (GDPR) | 730 days (2 years) | `retention_audit_log_days` | `RETENTION_AUDIT_LOG_DAYS` | yes |

`0` is the documented sentinel meaning "keep forever". The Celery
task `enforce_data_retention` short-circuits any class whose
setting is `0`.

## Why these defaults

* **Episodic — 365 days.** This is the raw conversation-turn
  layer. One year is the default cap most users expect for a
  conversational memory product, and it lines up with the most
  common SOC2 / ISO 27001 retention floors for "transactional
  user data". Operators with longer-tail use cases can set
  `RETENTION_EPISODIC_DAYS=0` to disable.
* **Semantic & engrams — forever (0 days).** These are the
  distilled knowledge layers consolidated from episodic data.
  They are intentionally smaller, denser, and re-deriving them
  is expensive (it requires the original episodic rows, which
  may already be gone). Keeping them forever by default is the
  safe choice; an operator with a strict "delete everything
  after N days" data class can set the matching env var.
* **Audit logs — 730 days (2 years).** Aligns with the most
  common SOC2 / ISO 27001 retention floors for security audit
  trails. This is the only class where the floor is more
  important than the cost.

## How retention differs from GDPR delete

These two systems do different things and should not be
conflated:

* **Retention** (this document) is a *policy* that applies
  uniformly to all users. It runs on a schedule and deletes rows
  whose `created_at` is older than the configured cap. It does
  not check `user_id` — every user's data is treated the same.
* **GDPR delete** (P7-E4, soft-delete grace period) is a *user
  request*. A user calls `DELETE /memory/user/{id}/all`, which
  stamps `users.deletion_scheduled_for = now() + 30 days`. The
  `execute_scheduled_deletions` Celery task then hard-deletes
  every user-scoped row plus API keys, then sets
  `is_active = False` permanently. Retention does not run this
  cascade; GDPR delete does not honor the per-table retention
  cap.

A user can ask for GDPR delete on day 1 and have everything
purged 30 days later. A user can also keep their account for
years and still see their oldest episodic memories prune at the
365-day mark. Both layers run independently.

## Operator action: add the Beat schedule entry

`enforce_data_retention` is a Celery task. It is **not** wired
into the default Beat schedule on purpose — enabling it on an
existing deployment without operator opt-in could surprise users
who relied on the prior "keep forever" behaviour.

To enable weekly retention enforcement, add an entry to your
Celery Beat configuration. Example for `celery_app.conf.beat_schedule`:

```python
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    # Existing schedule entries (consolidate, scheduled deletions)
    # ...
    "enforce-data-retention-weekly": {
        "task": "app.tasks.enforce_data_retention",
        # Sundays at 03:00 UTC — outside business hours, after any
        # weekly batch jobs have completed.
        "schedule": crontab(day_of_week="sunday", hour=3, minute=0),
    },
}
```

Daily is also reasonable for high-throughput deployments. The
deletion is bounded by the index on `created_at`, so the time
cost is `O(rows-to-delete)`, not `O(table-size)`.

## How to verify the task ran

The task emits one structured log line per terminal outcome:

```
event=task_end task_id=<uuid> task_name=app.tasks.enforce_data_retention
  outcome=success duration_ms=<int>
  episodic_deleted=<int> audit_log_deleted=<int>
```

A row count of `-1` for any class is a sentinel meaning
"this class failed during the task run; see the preceding ERROR
log line for details". The other classes still ran — failures are
isolated per-class.

If you do not see a recent `enforce_data_retention` task_end
log line on the schedule cadence you configured, the Beat entry
is not firing. Check the Beat container's stdout for the
periodic task name and verify Beat itself is healthy.

## Per-class status (current code state)

| Class | Implemented in `enforce_data_retention`? | Notes |
|---|---|---|
| `retention_episodic_days` | yes | `DELETE FROM episodic_memory WHERE created_at < cutoff` |
| `retention_audit_log_days` | yes | `DELETE FROM gdpr_audit_log WHERE created_at < cutoff` |
| `retention_semantic_days` | not yet | Default 0 = keep forever; adding the wiring is a one-line addition to `retention_classes` in `app/tasks.py` if a future operator sets a non-zero value |
| `retention_engram_days` | not yet | Same as semantic |

This is intentional: enabling `retention_semantic_days` /
`retention_engram_days` requires deciding what "delete a
semantic memory whose source episodic memory has already been
deleted" means in your data model. We document it here rather
than ship a half-correct implementation.

## Disabling retention entirely

Set every retention env var to `0`:

```
RETENTION_EPISODIC_DAYS=0
RETENTION_SEMANTIC_DAYS=0
RETENTION_ENGRAM_DAYS=0
RETENTION_AUDIT_LOG_DAYS=0
```

The task itself can still be scheduled — every class will
short-circuit and the task will return `{}` with `outcome=success`.
This is the recommended posture for "I want the wiring in place
but no actual deletions yet".
