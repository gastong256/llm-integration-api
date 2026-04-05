# Manual verification

This document provides a focused manual validation flow for the critical challenge behaviors.

It is intentionally smaller than a full QA checklist. The goal is to verify the main R1, R2, and R3 flows, plus the most important resilience behaviors: cache hits, rate limiting, circuit breaker opening, streaming cancellation, and degraded Redis mode.

For the observability overlay and the guided live presentation flow, use [docs/demo.md](demo.md).

## Prerequisites

From the repository root:

```bash
docker compose up --build
```

Leave the stack running and execute the checks below against `http://localhost:8000`.

---

## 1. Health

```bash
curl http://localhost:8000/health
```

Expected:
- `200 OK`
- `status = "ok"`
- `redis = "ok"`
- `llm_circuit = "closed"`
- `models_loaded = ["v1", "v2"]`

---

## 2. Authentication

### Missing API key

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/infer \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello"}'
```

Expected:
- `401`

### Invalid API key

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: invalid-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello"}'
```

Expected:
- `401`

### Valid API key

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello"}'
```

Expected:
- `200`

---

## 3. R1 — `POST /v1/infer`

```bash
curl -s -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello world","config":{"temperature":0.7}}' | python3 -m json.tool
```

Expected:
- `200 OK`
- response contains:
  - `request_id`
  - `output`
  - `model = "gpt-4o"`
  - `usage.tokens_in`
  - `usage.tokens_out`
  - `latency_ms`
  - `cache_hit = false`

### Cache hit

Run the exact same request again:

```bash
curl -s -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello world","config":{"temperature":0.7}}' | python3 -m json.tool
```

Expected:
- `200 OK`
- `cache_hit = true`
- `output` identical to the first call
- `latency_ms` significantly lower than the first call

### Cache miss on config change

```bash
curl -s -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello world","config":{"temperature":0.9}}' | python3 -m json.tool
```

Expected:
- `cache_hit = false`

---

## 4. Rate limiting

```bash
for i in $(seq 1 61); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/infer \
    -H "X-API-Key: test-key-2" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"gpt-4o\",\"input\":\"rate limit test $i\"}")
  echo "Request $i: $CODE"
done
```

Expected:
- requests `1..60` return `200`
- request `61` returns `429`

Optional body check:

```bash
curl -i -s -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: test-key-2" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"one more"}'
```

Expected:
- `429`
- `Retry-After` header present
- body indicates rate limit exceeded

---

## 5. R2 — `POST /v1/infer/stream`

```bash
curl -i -N -X POST http://localhost:8000/v1/infer/stream \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello"}'
```

Expected:
- `200 OK`
- `content-type: text/event-stream`
- `cache-control: no-cache`
- `x-accel-buffering: no`
- progressive `data: {"token": "...", "index": N}` events
- final `data: [DONE]`

### Stream cancellation

```bash
timeout 0.5 curl -N -X POST http://localhost:8000/v1/infer/stream \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"cancel me"}'
```

Expected:
- stream is interrupted after ~0.5s
- app logs include `stream_cancelled` or equivalent cancellation event
- app logs still include `stream_audit`

---

## 6. R3 — `POST /v1/classify`

### Default model version (`v1`)

```bash
curl -s -X POST http://localhost:8000/v1/classify \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"input":"pricing is not accurate"}' | python3 -m json.tool
```

Expected:
- `200 OK`
- response contains:
  - `label`
  - `confidence`
  - `model_version = "v1"`
  - `latency_ms`

### Explicit `v2`

```bash
curl -s -X POST http://localhost:8000/v1/classify \
  -H "X-API-Key: test-key-1" \
  -H "X-Model-Version: v2" \
  -H "Content-Type: application/json" \
  -d '{"input":"pricing is not accurate"}' | python3 -m json.tool
```

Expected:
- `200 OK`
- `model_version = "v2"`

### Invalid model version

```bash
curl -i -s -X POST http://localhost:8000/v1/classify \
  -H "X-API-Key: test-key-1" \
  -H "X-Model-Version: v3" \
  -H "Content-Type: application/json" \
  -d '{"input":"test"}'
```

Expected:
- `400`
- clear error message indicating model version not found

---

## 7. Circuit breaker

Restart the stack with forced stub failures:

```bash
docker compose down
STUB_FAILURE_RATE=1.0 docker compose up --build
```

Then:

```bash
for i in $(seq 1 6); do
  RESP=$(curl -s -w "\n%{http_code}" -X POST http://localhost:8000/v1/infer \
    -H "X-API-Key: test-key-1" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"gpt-4o\",\"input\":\"circuit test $i\"}")
  echo "Request $i: $RESP"
done
```

Expected:
- first `5` requests return `504`
- request `6` returns `503`
- response includes `retry_after_s`
- `Retry-After` header is present

### Health while open

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expected:
- `llm_circuit = "open"` or `"half_open"` if enough time already passed

### Recovery

Wait ~65 seconds, then:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expected:
- `llm_circuit = "half_open"`

Reset to normal after this check:

```bash
docker compose down
docker compose up --build
```

---

## 8. SSE while circuit is open

With the circuit still open, or after reopening it:

```bash
curl -N -X POST http://localhost:8000/v1/infer/stream \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello"}'
```

Expected:
- stream returns a single SSE error event
- event contains `service unavailable`
- event includes `retry_after_s`
- stream closes immediately
- no `[DONE]` event is emitted

---

## 9. Redis degraded mode

Stop Redis while the app is still running:

```bash
docker compose stop redis
```

### Infer should fail gracefully

```bash
curl -s -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"redis is down"}' | python3 -m json.tool
```

Expected:
- `503`
- service degradation error
- no stack trace in response

### Classify should still work

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/classify \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"input":"this should work"}'
```

Expected:
- `200`

### Health should show degradation

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expected:
- `status = "degraded"`
- `redis = "down"`

Restart Redis:

```bash
docker compose start redis
```

Expected:
- next requests recover automatically

---

## 10. Notes

- This file is meant for focused manual verification of the main challenge behaviors.
- It complements the README examples; it does not replace automated tests.
- For local development and tests outside Docker, run `make setup` first.
