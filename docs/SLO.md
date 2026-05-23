# Nexmem Service Level Objectives (SLOs) and Alerting

> **Scope.** Defines the availability, latency, and error-rate targets the Nexmem backend commits to during private beta, and the corresponding alerts the operator should configure when monitoring is live.
>
> **Status convention.** Every SLO is marked one of:
> - **DEFINED** — target written down here, no enforcement mechanism yet.
> - **ENFORCED** — alerts are wired in Sentry / Prometheus / Grafana and pages an on-call rotation.
>
> **Owner.** Backend on-call. Operator owns wiring SLOs into the monitoring tools and into the on-call rotation.
>
> **Related.** `docs/runbooks/POSTGRES_OUTAGE.md`, `docs/runbooks/REDIS_OUTAGE.md`, `docs/runbooks/OPENAI_OUTAGE.md`, `docs/runbooks/BACKUP_RESTORE.md`, `BACKEND_RISKS.md` (R-301 Redis fail-open).

---

## 1. Service definition

For SLO purposes the Nexmem service is the FastAPI HTTP surface backed by the canonical hardening stack (see `KIRO_SESSION_BOOTSTRAP.md` Section 3). The Celery worker, Redis, OpenTelemetry collector, and Sentry are dependencies — their availability rolls into the API SLOs below rather than being separately committed.

The `/health/live` endpoint is intentionally excluded from the SLO measurement window. It is fast and unconditional; if it 503s, the platform is hard down and the SLO measurement is moot.

---

## 2. Private-beta SLOs

All four SLOs below are **DEFINED** at this stage. None are **ENFORCED** — the alert rules in Section 4 describe what enforcement would look like once the operator wires it up.

### 2.1 Availability

| Metric | Target | Status |
|---|---|---|
| Successful HTTP responses on `/api/v1/**` (non-5xx) over a 30-day rolling window | **99.5%** | **DEFINED** |

99.5% allows roughly **3 hours 36 minutes** of downtime per 30-day month. This is conservative for the controlled private-beta cohort and will be tightened to 99.9% before public launch.

The denominator excludes:
- Requests that 4xx (client error — not a service availability concern).
- Requests during a documented `READ_ONLY=true` window when the operator has paged users about a planned freeze.

### 2.2 Write latency

| Metric | Target | Status |
|---|---|---|
| p95 latency for `POST /api/v1/memory/episode/write` measured by the Prometheus FastAPI instrumenter | **< 500 ms** | **DEFINED** |

The route runs NLP precompute and four DB writes; 500 ms p95 is comfortable on Render Pro + Supabase Pro with the bounded `embedder` / `nlp` pools (see `app/core/concurrency.py`). The breakdown budget is roughly:
- 200 ms — embedding + NLP (bounded executor)
- 100 ms — vector write + 3 row writes inside one transaction (`app/database.py` per-statement timeout 30 s caps the worst case)
- 100 ms — middleware overhead (slowapi, body-cap, JSON guard, structured logging)
- 100 ms — buffer

p99 is not committed at private beta. It will be added before public launch.

### 2.3 Read latency

| Metric | Target | Status |
|---|---|---|
| p95 latency for `POST /api/v1/memory/context` measured by the Prometheus FastAPI instrumenter | **< 400 ms** | **DEFINED** |

`/memory/context` does the cheaper read path (vector kNN + episodic LIMIT + JOIN) without any LLM round-trip. The 400 ms ceiling assumes an HNSW index hit; a cold-start first request (which loads the embedder model) will exceed this. The cold-start path is excluded from the SLO denominator until P5-C5 (cold-start histogram, P8-F5) is shipped.

### 2.4 Error rate

| Metric | Target | Status |
|---|---|---|
| 5xx responses as a fraction of all `/api/v1/**` responses, measured per rolling hour | **< 1%** | **DEFINED** |

5xx categories that count:
- 500 (unhandled internal error)
- 502 / 504 (upstream / gateway — should never originate from the FastAPI process itself, but counts if surfaced to clients)
- 503 from the rate-limiter, the read-only kill switch, or the OpenAI circuit breaker (P6-D7) — these are deliberate degradations and **do** count against the SLO so we cannot hide a real availability problem behind a perpetual `READ_ONLY=true` flag.

5xx categories that do **not** count:
- 5xx during a documented `READ_ONLY=true` window the operator has paged users about (same exclusion as 2.1).
- 503 from quota exhaustion (`app/core/quotas.py::_check_and_increment`) — this is a billing signal, not a service health signal.

---

## 3. Why these targets, and what changes for public beta

| Phase | Availability | Write p95 | Read p95 | Error rate |
|---|---|---|---|---|
| **Private beta (current)** | 99.5% | 500 ms | 400 ms | < 1% / hr |
| **Public beta (target)** | 99.9% | 300 ms | 200 ms | < 0.1% / hr |
| **GA (target)** | 99.95% | 200 ms | 150 ms | < 0.05% / hr |

Tightening will require P8-F5 (cold-start latency histogram so first-request outliers don't pollute the p95), P8-F1 (already shipped in Block 4 — distributed tracing so we can identify which stage of a slow request is the bottleneck), and a load-test pass against a production-shaped instance (deferred per `BACKEND_RISKS.md` R-204).

---

## 4. Alerting rules

The alerts below are **DOCUMENTED** at this stage. Actual configuration in Sentry, Prometheus, Grafana, or any other tool is **an operator action** — Kiro does not wire monitoring tools.

Each alert has:
- **Trigger** — the metric and threshold.
- **Severity** — `page` (immediate human attention) or `warn` (notification only, no on-call wakeup).
- **Runbook** — the document the on-call should open first.
- **Status** — `DOCUMENTED` until the operator wires it in a tool, then `ENFORCED`.

### 4.1 Alert 1 — Write latency regression

| Field | Value |
|---|---|
| Trigger | p95 latency on `POST /api/v1/memory/episode/write` > **500 ms** for **5 minutes** |
| Severity | **page** |
| Runbook | `docs/runbooks/POSTGRES_OUTAGE.md` (most likely cause: pool exhaustion or stuck query); `docs/runbooks/OPENAI_OUTAGE.md` if the embedding precompute path is degraded |
| Source metric (when wired) | `http_request_duration_seconds{handler="/api/v1/memory/episode/write"}` from `prometheus-fastapi-instrumentator` |
| Status | **DOCUMENTED** |

### 4.2 Alert 2 — Error-rate spike

| Field | Value |
|---|---|
| Trigger | 5xx responses on `/api/v1/**` > **1% per hour** |
| Severity | **page** |
| Runbook | The triage flow in any of the three outage runbooks, depending on which dependency is failing in `/health/ready` |
| Source metric (when wired) | Sentry "issue rate" alert OR `rate(http_requests_total{status=~"5.."}[1h]) / rate(http_requests_total[1h]) > 0.01` |
| Status | **DOCUMENTED** |

### 4.3 Alert 3 — Health probe down

| Field | Value |
|---|---|
| Trigger | `GET /health/ready` returns non-200 (any reason) |
| Severity | **page immediately** (no soak period) |
| Runbook | The runbook keyed by which dependency the response body lists as `error: ...` (Postgres / Redis / embedder) |
| Source metric (when wired) | Render's built-in HTTP health check, OR a synthetic Pingdom / UptimeRobot probe hitting `/health/ready` every 60 seconds |
| Status | **DOCUMENTED** |

### 4.4 Alert 4 — Redis connection lost

| Field | Value |
|---|---|
| Trigger | `/health/ready` reports `checks.redis.error` for any 60-second window |
| Severity | **page immediately** — see `BACKEND_RISKS.md` R-301 (Redis fail-open allows auth/rate-limit bypass during outage) |
| Runbook | `docs/runbooks/REDIS_OUTAGE.md` |
| Source metric (when wired) | Same `/health/ready` synthetic probe as Alert 3, parsing the JSON body to extract the `redis` field |
| Status | **DOCUMENTED** |

This alert is intentionally separate from Alert 3 because the response to a Redis-only outage on a multi-replica deploy is **different and time-sensitive** — see R-301 mitigations and `REDIS_OUTAGE.md` Section 3.2 for the `READ_ONLY=true` decision matrix. A combined Alert 3 would lose that signal.

### 4.5 Alert 5 — Celery queue depth

| Field | Value |
|---|---|
| Trigger | Celery `consolidation` queue depth > **1000 tasks** for **10 minutes** |
| Severity | **warn** (Slack notification, no on-call page) |
| Runbook | `docs/runbooks/REDIS_OUTAGE.md` (worker may be stuck retrying broker connections); `docs/runbooks/OPENAI_OUTAGE.md` (consolidation falls back to raw content fallback during OpenAI outages, which keeps the queue moving — if depth is climbing despite the breaker being CLOSED, look at the per-task structured logs from P8-F2/P6-D10 for the actual failure) |
| Source metric (when wired) | Redis `LLEN celery` (broker queue), exposed via `prometheus-redis-exporter` or a custom probe |
| Status | **DOCUMENTED** |

---

## 5. Things explicitly NOT alerting yet

To avoid alert fatigue we deliberately do not page on:

- Per-task Celery failures — `P8-F2`/`P6-D10` task logs surface them in the log shipper; only sustained queue growth (Alert 5) or DLQ growth pages.
- DLQ size — separate metric to be added in Block 5+; for now operators check it manually with `python scripts/dlq_admin.py list --limit 20`.
- OpenAI circuit-breaker `OPEN` events — the breaker is the documented graceful-degradation path; the resulting 503s on `/rag/chat` already show up in Alert 2 (error-rate spike) if they exceed the SLO threshold. A separate breaker alert would double-count.
- Cold-start latency outliers — pending P8-F5 (cold-start histogram) before they can be safely separated from steady-state p95.

---

## 6. Operator actions to ENFORCE the SLOs

When the operator is ready to flip the four SLOs from **DEFINED** to **ENFORCED**:

1. Enable the Prometheus instrumenter scrape (`/metrics` is already token-gated by `METRICS_SECRET_KEY`; just enable the scrape target).
2. Set up Grafana dashboards for the four SLOs with the recording rules implied above.
3. Wire the five alerts in Section 4 to PagerDuty / Slack / your on-call tool.
4. Update this document — change every `**DEFINED**` to `**ENFORCED**` and add the date the alert went live.
5. Add the dashboard URL and the alert-rule URLs to `docs/INCIDENT_RUNBOOK.md` so on-call can find them in the moment.
