# DECISIONS.md

Running log of decisions I made while building this. Cleaned up in the final pass.

---

## 1. Architecture basics

**FastAPI + uvicorn**

Went with FastAPI because I need async natively — LLM calls are pure I/O and blocking them on a thread pool would be wasteful. Also gets me Pydantic v2 and OpenAPI docs for free.

- Considered bare Starlette but FastAPI's DI system pays for the thin wrapper
- Flask was never really on the table for this load profile

**Service layer**

Keeping business logic out of routers. Routes stay thin: extract inputs, call service, return response. Mainly doing this so I can unit-test services without an HTTP client. The inference service will also be called from both sync and streaming routes, so it needs to be reusable anyway.

`InferenceService.infer()` ended up at about 80 lines — above my usual 30-line target once request collapsing was added. I'd split it into `_check_cache`, `_call_with_collapsing`, and `_build_response`. Didn't do that now because the method works end to end and splitting a tested flow right before submission is a good way to introduce bugs.

**Single process, not microservices**

One FastAPI app + one Redis. No Kafka, no Celery, no separate worker processes. The challenge requires `docker-compose up` with zero configuration — adding a broker or a worker service would break that immediately. One process with asyncio handles the concurrency instead.

Trade-off: single process limits horizontal scaling. In production I'd run multiple replicas behind a load balancer — Redis already handles all the shared state (cache, rate limit counters, CB if moved there), so replicas are stateless and can scale independently.

**Redis down behavior**

When Redis goes down, I chose to fail explicitly on Redis-dependent paths instead of pretending everything is fine. Rate limiting without shared state is meaningless once there is more than one instance anyway, so `/v1/infer` returns 503 if the limiter can't talk to Redis.

Cache is different. No Redis just means no shared cache, so I treat cache get/set failures as misses and still let inference continue after the circuit breaker check. `/v1/classify` keeps working because it doesn't need Redis at all, and `/health` reports `status=degraded` with `redis=down` instead of crashing. If I had stricter uptime requirements, I'd consider a short in-memory cache fallback as a grace period, but not an in-memory rate limiter.

**pydantic-settings**

All config in one `Settings` class, validated at startup. Bad env var → crash early with a clear message, not a silent wrong value at runtime. `get_settings()` cached with `lru_cache`, injected via `Depends()`.

**structlog + JSONRenderer**

JSON from day one. `merge_contextvars` lets me bind `request_id` in middleware and have it appear in every log line within that request without threading it through manually. stdlib logging with a JSON formatter would work but doesn't give me that for free.

---

## 2. Caching and rate limiting

**Sliding window, not fixed window**

The simple alternative is `INCR + EXPIRE` — one key per client, increment on each request, expire after 60 seconds. Two Redis ops, trivial to understand. The problem is it resets at a fixed clock boundary, so a client can exhaust the limit at :59 and immediately exhaust it again at :00, getting 2× the allowed burst in two consecutive seconds.

Sorted set + `ZREMRANGEBYSCORE` fixes this. Every `check()` call evicts entries older than `now - 60` before counting, so the window always reflects the actual last 60 seconds regardless of what the clock says. More ops per request (5 vs 2), but the correctness guarantee is worth it for an API with strict SLAs.

One deliberate choice: rejected requests don't count toward quota (`ZREM` on denial). A hammering client gets a stable `retry_after_s` based on when real allowed requests expire — not an ever-growing penalty from their own rejected calls.

**SHA-256 content-addressed cache keys**

Cache key is SHA-256 of `json.dumps({model, input, config}, sort_keys=True)`. The `sort_keys=True` means `{"temp": 0.5, "max": 100}` and `{"max": 100, "temp": 0.5}` are treated as the same request and hit the same entry. Simple string concatenation would miss this and cause redundant LLM calls for identical requests.

SHA-256 over a shorter hash because a false cache hit (serving the wrong response) is a correctness bug, not a performance miss. The collision probability of SHA-256 in this context is negligible.

**SHA-256 over embeddings**

"SemanticCache" as a name implies vector similarity to most readers — cache near-identical queries, not just exact ones. I went with exact-match instead. Two reasons: the challenge defines the cache key as model + input + config, so fuzzy matching is out of scope by design. And adding an embedding model means a vector DB or an embedding API call on every request path — a whole dependency stack for zero benefit given the stated requirements. SHA-256 of the normalized payload is the right call here.

**Request collapsing with Redis lock**

I added a small Redis `SETNX` lock in front of the LLM call for cache misses. First request becomes the leader and does the expensive work. Followers just poll the cache for a short window and reuse the result once it lands.

Without this, a thundering herd on the same prompt turns one cache miss into a pile of identical upstream calls. The trade-off is a little more Redis traffic on misses plus a short polling loop for followers. I kept it intentionally simple: short lock TTL, bounded wait, and if Redis is down the service just skips collapsing and behaves like before.

`RequestCollapser` currently reaches into `cache._redis` and calls `cache._make_key()` from outside the class. In a refactor I'd inject it from the lifespan as a standalone dependency with its own Redis reference. Didn't change it now because the flow works and restructuring the dependency graph this close to submission is risky.

---

## 3. Load scenarios

### Scenario A — Thundering herd (implemented)

Redis SETNX lock per cache key. The first request to arrive on a cache miss acquires the lock and calls the LLM. Every other request for the same input sees the lock, polls the cache on a 50ms interval, and returns the result as soon as it appears — max 15s wait.

Result: 1 LLM call instead of N. The lock carries a 10s TTL so a crashed leader doesn't deadlock followers indefinitely. Followers that hit the wait timeout fall back to calling the LLM themselves.

### Scenario B — LLM provider down (documented)

Covered by the circuit breaker. 5 failures inside a 30s window opens the circuit for 60s. While open, `/v1/infer` and `/v1/infer/stream` return 503 immediately with a `retry_after_s` countdown — clients know exactly when to retry.

`/v1/classify` keeps working normally because it uses the local model, not the LLM provider. `/health` reports `llm_circuit: "open"` so monitoring can see the state.

### Scenario C — Slow consumer streaming (documented)

No custom buffer needed. When a client reads slowly, the kernel TCP send buffer fills, uvicorn's write calls block, and the async generator's `yield` pauses as a result. Uvicorn propagates the backpressure naturally.

The request timeout (default 30s via `LLM_TIMEOUT`) is the final safety net. If the client stalls long enough to exceed it, the generator catches the cancellation and closes the upstream LLM connection. No orphaned streams, no memory growth.

### Load testing observations

Ran Locust at ~100 req/s against the stub adapter with 0 failures. One thing to watch: the default `RATE_LIMIT_RPM=60` kicks in fast and dominates results before you see anything interesting about throughput or latency. For real load tests, raise it to 10000 or use dedicated load-test API keys with relaxed limits. In production I'd have a separate key class for this.

---

## 4. LLM adapter design

**httpx async-first, never `requests`**

Sync `requests` blocks the entire event loop for the duration of the HTTP call. At 300 req/s, a 500ms LLM call would stall every other in-flight request. `httpx.AsyncClient` integrates natively with asyncio — the loop keeps serving other requests while waiting for the LLM. This is non-negotiable.

Timeout is globally configurable via `LLM_TIMEOUT` env var (default 30s). Per-request override via the `config` dict would be a natural next step but I left it out to keep the scope focused. The stub ignores timeout since it controls its own latency directly.

**Stub as default, no real provider needed**

Default adapter is a deterministic stub. `LLM_ADAPTER=http` switches to a real provider. The stub lets `docker-compose up` work without any credentials — that's a hard requirement for this challenge. Either way the service sees the same interface, doesn't matter what's behind it.

**Usage field names**

The HTTP adapter returns provider-native keys (`prompt_tokens`, `completion_tokens`) because that's what the provider actually sends. The service layer remaps them to the API contract (`tokens_in`, `tokens_out`) before building the response. This keeps the adapter honest about what the provider returns and centralizes the mapping in one place.

**Configurable failure rate**

`STUB_FAILURE_RATE` (0.0–1.0) lets me demo the circuit breaker without a real provider. Set it to 0.8, fire 10 requests, watch the circuit open. Without this, validating CB behavior would require either a flaky real provider or manual code changes. Worth the one extra config field.

**Adapter for LLMs, dict registry for ML models**

LLM providers differ enough (stub, HTTP, different APIs) to justify an ABC. ML models don't — both versions are sklearn pipelines with the same `predict()` interface. A dict `{version: model}` looked up by `X-Model-Version` is simpler and more honest than wrapping identical objects in adapters. I'd only add an adapter layer for ML models if serving grew to include ONNX, Triton, or remote inference.

**Models load in lifespan with `to_thread`**

Loading joblib files is blocking disk I/O plus deserialization, so I didn't want it sitting directly on the event loop during startup. I load both model files with `asyncio.to_thread()` and kick that work off in parallel with the Redis dependency setup using `asyncio.gather()`.

Trade-off is a slightly busier startup path, but the app only starts serving after both models are ready. I'd rather pay that one-time cost than let the first classify request discover a missing model lazily.

**`model.predict()` also goes through `to_thread`**

sklearn prediction is CPU-bound, even if it's short. So I kept that work off the event loop too and made the classify service await the registry instead of calling the pipeline directly from the request path.

There's a tiny thread-hop cost, but it's much better than letting concurrent requests pile up behind a blocked loop.

**Circuit breaker in-memory, not in Redis**

State lives in the process behind an `asyncio.Lock`. For a single-worker deploy it's correct and fast — no Redis round-trip on every request to check CB state.

The trade-off is obvious: in multi-worker production, each worker has its own failure count. Worker A might open its circuit while Worker B hasn't hit the threshold yet. The fix would be Redis with a Lua script for atomic check-and-set across workers. Not needed here, but that's the natural evolution path.

**Metrics middleware trade-off**

The metrics middleware reads and reconstructs `/v1/infer` response bodies to count cache hits and circuit-open rejections. Cleaner approach would be incrementing the counters directly in `InferenceService`. I kept the middleware approach to avoid importing Prometheus counters into the service layer. Trade-off: double body read on every `/v1/infer` request. Negligible for small JSON responses.

**SSE audit log in generator's finally, not BackgroundTasks**

The complete stream is accumulated in a list as tokens arrive. In the generator's `finally` block, `asyncio.create_task()` dispatches the audit log write.

FastAPI's `BackgroundTasks` won't reliably execute when a `StreamingResponse` is cancelled — if the client disconnects mid-stream, the `CancelledError` kills the task before background hooks run. The `finally` block of an async generator always runs, even on cancellation. That's the guarantee I needed.

Trade-off: `create_task` is fire-and-forget. If the audit log write fails, it shows up in error logs but doesn't affect the response. Fine for observability; I wouldn't use this pattern for anything critical.

**Cancellation propagates upstream**

The streaming generator checks `request.is_disconnected()` before yielding each chunk and catches `asyncio.CancelledError`. Either way, the generator stops iterating and the adapter's async generator gets garbage-collected, closing the underlying connection.

Without this, a disconnected client leaves the LLM call running until it finishes. That wastes tokens, holds a connection, and pollutes timing metrics. The overhead of one `is_disconnected()` check per chunk is negligible.

**retry_after_s is remaining time, not a fixed value**

When the circuit is open, `get_retry_after()` returns `recovery_timeout - (now - opened_at)`. It starts near 60 and counts down. A client that polls every few seconds gets a decreasing value and knows exactly when to retry.

The spec example shows `retry_after_s: 45` with a 60s timeout. The only way those two numbers are consistent is if the circuit opened 15s earlier — the field is remaining time, not total timeout. Dynamic is also just more useful.

---

## Development tooling note

I used AI assistants (Copilot and Claude) for some of the mechanical parts — generating the synthetic training dataset, the Locust script skeleton, and early README structure. Mostly on the bonus sections where the interesting problem is design, not typing.

Architecture decisions, resilience patterns, and the core implementation are my own work. Knowing where AI saves time without compromising quality is part of working well at this point — same category as Stack Overflow, starter templates, or copy-pasting your own old code.
