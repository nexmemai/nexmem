# Runbook: OpenAI outage

> **Scope.** What to do when the OpenAI API (or our wrapping rate-limit budget) is failing for sustained periods, breaking the RAG path (`/api/v1/rag/chat`) and the consolidation Celery task. The application has explicit graceful-degradation paths for both flows; this runbook walks the operator through verifying they are working as designed.
>
> **Owner.** Backend on-call. Escalation rare — the system is designed to absorb a multi-hour OpenAI incident without operator intervention.
>
> **Related.** `app/core/circuit_breaker.py` (the breaker itself), `app/services/llm.py` (RAG path), `app/services/consolidation.py` (Celery path), `tests/test_circuit_breaker.py`.

---

## 1. Symptoms

What end-users / operators see:

- `/api/v1/rag/chat` returns **HTTP 503 with `Retry-After: <cooldown>`** when the circuit is OPEN. The body is the generic Phase-7 sanitised error from `app/routers/rag.py`. No internal stack trace is leaked.
- `/api/v1/rag/chat` returns degraded-but-successful responses **before** the breaker trips: tenacity retries each call up to 3× with exponential backoff. Latency on these requests can reach 30–60 seconds.
- Consolidation Celery tasks log `consolidation: openai circuit OPEN; using raw content fallback` (see `app/services/consolidation.py`). The task **succeeds** with a non-LLM-summarised engram. No `gpt-4o-mini` summary is produced for that batch; the raw episodic content is stored as the engram body until OpenAI recovers and the next consolidation tick re-summarises.
- Sentry: spikes in `openai.APIError`, `openai.APIConnectionError`, `openai.RateLimitError`. After the breaker trips, those errors stop entirely (the circuit short-circuits before the API call) and `CircuitOpenError` warnings dominate.
- `/health/ready` continues to return 200 — the embedding service probe (HuggingFace `all-MiniLM-L6-v2`, 384-d) does **not** depend on OpenAI.

What end-users on `/api/v1/memory/*` (writes), `/auth/*`, or any non-RAG endpoint see: **nothing**. OpenAI is not in the request path for those routes.

---

## 2. The circuit breaker (already shipped, P6-D7)

`app/core/circuit_breaker.py` provides a process-local breaker registered under the name `"openai"`. Both LLM call sites use it:

| Path | Site | Behaviour on OPEN |
|---|---|---|
| HTTP RAG | `app/services/llm.py::generate_rag_response` | Raises `CircuitOpenError` → router translates to **HTTP 503** with `Retry-After`. |
| Celery | `app/services/consolidation.py::_summarize_with_openai` | Caught explicitly → falls back to raw episodic content; task **completes**, no retry storm. |

Defaults from `app/config.py`:

| Setting | Default | Tunable env |
|---|---|---|
| `circuit_openai_failure_threshold` | 5 consecutive failures | `CIRCUIT_OPENAI_FAILURE_THRESHOLD` |
| `circuit_openai_failure_window_seconds` | 60s window | `CIRCUIT_OPENAI_FAILURE_WINDOW_SECONDS` |
| `circuit_openai_cooldown_seconds` | 60s open | `CIRCUIT_OPENAI_COOLDOWN_SECONDS` |

State machine: **CLOSED → OPEN** at threshold; **OPEN → HALF_OPEN** after cooldown; **HALF_OPEN → CLOSED** on first success or **HALF_OPEN → OPEN** on first failure.

---

## 3. Immediate actions (first 5 minutes)

### 3.1 Confirm

Check OpenAI status: <https://status.openai.com>. If the upstream is reporting an incident, the breaker is doing its job — proceed to Section 4 to verify graceful degradation, but do **not** disable the breaker.

Confirm the breaker tripped:

```bash
# Render dashboard → Web Service → Logs. Filter for:
"circuit_breaker.openai: CLOSED -> OPEN"
"circuit_breaker.openai: HALF_OPEN -> OPEN"
"circuit_breaker.openai: HALF_OPEN -> CLOSED"
```

Each transition is logged at WARNING with the failure-window context.

### 3.2 Verify graceful degradation is happening

```bash
curl -i -X POST https://nexmem-api.onrender.com/api/v1/rag/chat \
  -H "Authorization: Bearer $TEST_USER_JWT" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"<test_user_id>","message":"hello"}'
```

Expected during an OPEN-circuit window:
- `HTTP/1.1 503 Service Unavailable`
- `Retry-After: <0..60>` header
- Body: a short `{"detail": "..."}` with no internal stack content (P7-E9)

If RAG returns 500 instead of 503, the router has lost the `CircuitOpenError` translation — see Section 6.1.

### 3.3 Verify consolidation is using the raw fallback

```bash
# Render dashboard → Worker (celery-worker) → Logs. Filter for:
"consolidation: openai circuit OPEN; using raw content fallback"
```

Each consolidation cycle for the OPEN window should emit one such line per user. If you instead see `consolidate_user_memory_task` failing with an unhandled `openai.APIError`, the consolidation circuit-guard is broken — see Section 6.2.

### 3.4 (Usually unnecessary) Notify users

Most outages are short and absorbed by the breaker. If RAG `503`s persist for >15 minutes, post:

> Our LLM provider is degraded, so chat-style features (`/rag/chat`) are intermittently returning 503. All other Nexmem features (memory writes, recall, profile, graph) are unaffected.

---

## 4. Diagnosis tree

### 4.1 Is the failure rate-limit-driven?

```
openai.RateLimitError → circuit trips at 5 in 60s.
```

Mitigation: confirm the OpenAI org has the right quotas (Usage tab on the OpenAI dashboard). For `gpt-4o`, organisations on lower tiers throttle aggressively under bursty traffic. Increase the org's spending cap or upgrade the tier.

### 4.2 Is the failure auth-driven?

```
openai.AuthenticationError → circuit trips at 5.
```

`OPENAI_API_KEY` was rotated, revoked, or set to a placeholder. Recovery: re-set it in Render env vars and trigger a deploy. The breaker will close on the next successful call (HALF_OPEN probe).

### 4.3 Is the failure model-availability-driven?

```
openai.NotFoundError: model "gpt-4o" not found
```

The model alias was deprecated. Update `settings.openai_llm_model` (RAG) and `settings.consolidation_llm_model` (Celery) to a current alias.

### 4.4 Is the cooldown too aggressive?

If you see frequent CLOSED↔OPEN flapping after OpenAI recovers, the failure window or cooldown may be tuned too tightly for the noise floor. Tweak via env vars (Section 2). Defaults are conservative and suitable for the first paying customers; cosmetic tuning is rarely worth a deploy.

---

## 5. Recovery procedure

OpenAI recovery is mostly automatic. The operator's job is to verify, not to fix.

### 5.1 Verify circuit closes

After OpenAI's status page reports recovery, the next request after `circuit_openai_cooldown_seconds` (default 60s) flips to HALF_OPEN. The first successful call closes the circuit.

```bash
# Render dashboard → Web Service → Logs. Look for:
"circuit_breaker.openai: HALF_OPEN -> CLOSED"
```

If you do not see this line within 5 minutes of OpenAI recovery, it usually means traffic dried up entirely — issue a synthetic RAG request to nudge the breaker.

```bash
curl -i -X POST https://nexmem-api.onrender.com/api/v1/rag/chat \
  -H "Authorization: Bearer $TEST_USER_JWT" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"<test_user_id>","message":"runbook recovery probe"}'
```

Expected: HTTP 200 with a short LLM response.

### 5.2 Confirm consolidation backlog clears

When the breaker is OPEN, consolidation tasks finish but with a raw-text engram (no LLM summary). They are **not** retried automatically once the circuit closes — the work was completed, just degraded.

To re-summarise the affected window:

```bash
# Render dashboard → celery-worker → Shell tab.
python -c "from app.services.scheduler import trigger_consolidation; trigger_consolidation()"
```

This re-runs consolidation for every user. The next pass will replace the raw fallback engrams with LLM-summarised ones (the consolidation logic deduplicates by content hash + user, so re-running is safe and idempotent).

### 5.3 Spot-check Sentry

The number of `openai.*Error` events in the last hour should drop to zero (or to the baseline noise level) once the circuit closes. If not, the cooldown was too short and the breaker is flapping; either reduce traffic temporarily or lengthen `circuit_openai_cooldown_seconds`.

---

## 6. Things that should not happen — escalate if they do

### 6.1 RAG returns HTTP 500 (not 503) during the outage

The router lost the `CircuitOpenError → 503` translation. Until fixed, manually disable RAG with read-only mode (it 503s every state-changing route, which fails-safe but is heavy-handed). Open a P0 issue.

### 6.2 Consolidation fails with unhandled exception during the outage

The breaker guard around `_summarize_with_openai` is broken. Suspend the celery-worker (DLQ will queue tasks) and open a P0 issue.

### 6.3 Breaker never trips even with sustained failures

Check `_breakers` registry — a custom `name="openai"` breaker can be created without picking up settings. Confirm via `app/core/circuit_breaker.py::get_breaker("openai").failure_threshold == 5`.

### 6.4 Breaker trips on a transient blip and stays open for hours

Cooldown is too long, or the half-open probe keeps failing. Hand-reset:

```bash
# Render dashboard → Web Service → Shell tab.
python -c "from app.core.circuit_breaker import reset_all_breakers; reset_all_breakers()"
```

Note: the breaker is **process-local**. A reset in the web service does not reset the worker breaker, and vice-versa.

---

## 7. Post-incident checklist

- [ ] Save the OpenAI status page snapshot for the incident window.
- [ ] If the breaker flapped, file an issue tagged `reliability` to retune `circuit_openai_*` defaults.
- [ ] If RAG was unavailable for >1 hour, drop a note in `KIRO_WORK_LOG.md` Section 6 (limitations) so the next session knows it happened.
- [ ] Confirm on the next consolidation cycle that the raw-fallback engrams from the OPEN window have been re-summarised (`SELECT count(*) FROM engrams WHERE summary_source='raw_fallback';` should drop after 5.2).
