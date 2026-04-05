# DECISIONS.md

Working log of the decisions I made while building this.

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

**Rate-limit headers without changing the error contract**

I added `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` on successful infer responses and relevant `429`s, but kept the current JSON body and `Retry-After` behavior intact. That gives clients something standard-ish to read and makes the limiter easier to demo, without turning this into a bigger API contract rewrite.

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

Measured against the full app with `RATE_LIMIT_RPM=10000`. The point latencies below come from the API's own `latency_ms` field, so they reflect app-side processing time rather than full client-observed RTT.

| Scenario | p50 | p95 | Notes |
| --- | ---: | ---: | --- |
| `/v1/infer` cache miss | ~152 ms | — | 40 samples |
| `/v1/infer` cache hit | ~0.2 ms | — | 40 samples |
| `/v1/classify` | ~1 ms | — | 40 samples |
| Locust, 8 users | ~7 ms | ~11 ms | mixed workload, ~166 req/s, 0 failures |

Main signal is the gap between infer cache miss and cache hit. On the mixed Locust workload, the service sustained ~166 req/s with 0 failures at 8 users. At 100 users it still returned 0 failures, but latency degraded sharply instead of holding flat: aggregate p50 was ~85 ms, aggregate p95 was ~1000 ms, and `/v1/infer` median was ~1100 ms. So the useful read is "kept serving under pressure", not "scales cleanly to 100 users".

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

**Adapter for LLMs, small SDK boundary for ML models**

LLM providers still sit behind an ABC because the integration points really differ. Local classify models now sit behind a small internal wrapper contract in `sdk/`, with service-specific implementations in `app/models/`. That gives me one stable classify boundary without pretending these wrappers are already a separate published library.

I kept that SDK internal on purpose. I wanted a real boundary and a real migration path, not the overhead of packaging and versioning another artifact before a second service exists.

The reusable part is just the generic wrapper contract plus the registry. `app/core/model_registry.py` only wires local wrappers into that registry and resolves them by version. So there isn't a second classify registry hiding in the app layer.

The wrapper schemas are part of the runtime path now instead of metadata on the side. `ClassifyService` builds the payload through `wrapper.input_schema`, and the wrapper itself is the authority for returning the right output model. The old direct predict path stays gone.

**Preprocess and postprocess live with the wrapper**

Input normalization and output shaping live in the local sentiment wrapper layer instead of leaking into `ClassifyService`. So the service just resolves the wrapper, builds the payload, calls `predict()`, and maps the result to the HTTP response.

For this project I kept the preprocessing simple: string cleanup plus output normalization. That felt more honest than dragging in a heavy dependency just to prove the wrapper can do prep work. The important part is where that logic lives, not making it look fancier than the model actually needs.

If this grew into more structured feature prep or batch-oriented work later, this same model layer is where I'd use tools like pandas. I just didn't want to force that into a one-text request path that doesn't really need it.

Training and serving now line up around that same cleanup. The training script applies the same normalization logic conceptually before fitting, even though I kept that code duplicated on purpose instead of importing runtime modules into a one-off model-generation script.

The training data also moved away from the generic placeholder sentiment examples from `v1.0.0` and into small retail-observation phrases. That fits the company context better and still preserves the point of the two model versions: `v1` is unigram-based, `v2` sees bigrams too, so negation cases like `pricing is not accurate` still split them in a useful way.

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

**Exception mapping is centralized, not route-by-route**

I moved the exception-to-response translation into global handlers and kept the services raising domain errors. That cleaned up the routes quite a bit and made the error behavior look deliberate instead of a pile of local `try/except` blocks.

I kept the public semantics the same on purpose: same `400/429/503/504` statuses, same `Retry-After` behavior where it already existed, and FastAPI's normal `422` body for validation. So the improvement is mainly in coherence and maintenance, not a contract rewrite.

**Uvicorn access logs are off for the demo path**

Uvicorn access logs are off in the local run path and in the container command. The app is already emitting structured JSON events with better context, so the plaintext access lines were mostly noise during the demo.

I didn't try to fully rewire Uvicorn logging into `structlog`. That felt like a lot of churn for very little gain here. Killing the noisy part was enough.

**Logs carry request and trace correlation together**

`request_id` was already useful inside the app. Once I started showing traces too, that wasn't enough on its own. Logs now carry `request_id` plus `trace_id` and `span_id` when a request is traced, using the active OpenTelemetry span at log emission time.

That keeps the implementation local and small, and it gives logs and Jaeger a shared handle instead of two parallel observability stories.

**Safe observability is narrow and intentional**

Bounded prompt/output visibility made more sense than either leaving payloads raw or dropping them entirely. The goal is to keep enough context to explain what happened while avoiding the sloppy "log everything" story.

The masking is intentionally narrow too. I only hide obvious keys like `api_key`, `token`, and `authorization`, and I only do it on the logging-visible payloads. That's enough maturity to talk about without pretending this branch includes a full privacy or compliance subsystem.

**gRPC is a design artifact here, not a second runtime**

A small `.proto` file is enough to show how classify could evolve toward an internal model-serving boundary over gRPC while keeping HTTP at the edge. That gives me something concrete to point at without bloating this branch with a second server, generated code, or a fake half-implementation.

The important part for this branch is the boundary, not the transport runtime. So HTTP still owns the gateway path, and gRPC stays as a design artifact for the next hop inward.

**The demo is scripted, not improvised**

A guided demo runner made more sense than relying on a pile of manual curls. The point is to make the presentation reproducible, lower operator error, and turn the branch into a narrative I can walk through scene by scene.

That does mean carrying one repo-local script whose value is mostly presentation, not product runtime. I'm fine with that trade-off because this branch is explicitly a demo evolution, and the script makes the observability and resilience story much easier to show live.

**Demo-specific guidance lives in `docs/demo.md`**

I kept the main README usable as a project entrypoint and pushed the scene order, live transitions, and observability talking points into a separate demo guide. That felt cleaner than turning the README into presenter notes.

---

## 5. Road to production

If I took this further, these are the next steps I see.

### Service

- Move circuit breaker state to Redis with a Lua script for atomic check-and-set across workers.
- Replace the custom request_id middleware with OpenTelemetry tracing so `traceparent` propagates across service boundaries and cache/CB/LLM calls show up as spans in Jaeger or Tempo.
- Put an LLM Gateway (LiteLLM or similar) between this API and providers for fallback chains, canary routing, and cost tracking. The `HttpLLMAdapter` already points at a configurable base URL, so that would mostly be a config change.
- Scale horizontally by moving the remaining in-process state (CB) to Redis. At that point replicas become fully stateless, and the models are small enough to load on every instance.
- Add multi-tenancy by mapping API keys to tenant context, then splitting cache namespaces and rate-limit tiers per tenant.
- Consider a semantic cache with embeddings (pgvector or Redis VSS) only once exact-match hit rates stop improving.

### Development workflow

- Add CI/CD in this order: lint → type check (mypy strict) → test (pytest-cov ≥ 80%) → build image → deploy staging → smoke test → promote.
- Automate semantic versioning from conventional commits with `python-semantic-release`.
- Harden pre-commit with `detect-secrets`, `commitlint`, `check-yaml`, and `mypy`.
- Add dependency updates with Renovate and security scanning with `pip-audit`.
- Harden the container with a distroless final stage, Trivy image scanning, and a read-only filesystem.

### Where this sits
```
Client / BFF
     │
     ▼
[RAG / Domain Service]    ← retrieves data, builds prompts, knows the business
     │
     ▼
[**This Inference API**]      ← auth, rate limit, cache, CB, model serving
     │
     ▼
[LLM Gateway]             ← provider routing, fallback, cost tracking
     │
     ▼
[Providers / local models]
```

Each layer scales independently. This API doesn't know what the domain is — it receives a prompt and returns a response. The business logic lives upstream.

---

## 6. Demo branch notes

**Observability as an overlay, not a base-stack mutation**

I kept Jaeger, Prometheus, Grafana, and the OTel collector in `docker-compose.observability.yml` instead of bloating the original `docker-compose.yml`. The challenge delivery path stays exactly where it was, and the demo branch becomes an additive overlay I can turn on when I want the full observability story.

Trade-off is one extra compose file and a slightly more complex startup command. Worth it because it lets me say the original submission is still intact and the demo branch is an evolution, not a rewrite.

**Provisioned dashboards, not click-ops**

Prometheus and Grafana are provisioned from repo files. Manual setup is fragile and easy to forget; repo-backed provisioning is boring in the best way and keeps the stack reproducible.

**OpenTelemetry, not Jaeger-specific wiring**

I used OpenTelemetry as the tracing layer and OTLP as the export path. Jaeger is just the backend I happened to plug in for this demo. That keeps the instrumentation portable if I ever want Tempo or Datadog later, and it avoids hard-wiring the app to one tracing vendor.

I considered just leaning on logs and metrics, or wiring straight to Jaeger-specific bits. Didn't love either. OTel is the cleaner boundary.

**Tracing stays opt-in**

I only initialize tracing when `OTEL_EXPORTER_OTLP_ENDPOINT` is present. So the base stack still behaves like the original delivery, and the extra tracing path only shows up when the observability overlay is enabled on purpose.

That felt better than making tracing a silent runtime dependency of the app all the time. The demo gets full traces; the base project stays clean.

I also pulled the tracing env vars into the main settings surface once the branch started carrying more config. The app runtime now reads one settings object for both normal behavior and the optional tracing path, while Locust-specific overrides stay local to the load script because they aren't app config.

**Manual spans stay close to the real gateway path**

I added a small set of manual spans around the parts I actually care about when explaining a request: cache check, circuit breaker check, LLM call, cache write, and response build. That reads much better in Jaeger than a pile of generic framework spans or every tiny helper call.

I could have traced more, but it would mostly add noise. For this demo I want the trace tree to be understandable in a few seconds.

I did factor the repeated tracing boilerplate into a tiny helper later on. But I kept `start_as_current_span(...)` in the services so the business flow still reads directly from the request path instead of disappearing behind decorators or middleware.

**Streaming traces focus on lifecycle, not per-token detail**

For SSE I kept the manual tracing at the lifecycle level: stream start, chunk activity, cancellation, and audit dispatch. That's enough to show the happy path and the cancellation path without turning one stream into a noisy trace full of token-level children.

If I ever needed to debug backpressure or token pacing in production, then I'd consider going deeper. Didn't feel worth it here.

**Cost stays as estimated cost from post-flight usage**

I kept cost as estimated cost derived from the usage numbers I already get back after a successful LLM call. That's enough for the demo and keeps the whole thing deterministic. I didn't want to drag in provider billing APIs or some external metering product just to say something useful about spend.

So the app now increments token counters and one estimated cost counter from the same normalized usage contract it already returns in `/v1/infer`.

**Input and output tokens stay split**

I kept separate token counters for input and output instead of one flat total. Pricing is usually different on both sides, so merging them would make the cost story weaker and harder to explain.

Could have pushed the cost math into Grafana from raw counters only. I didn't. I'd rather expose both the raw token dimensions and the estimated cost directly from the app.

**The dashboard stays compact and demo-oriented**

I kept the dashboard tight on purpose: request rate, p50/p95, cache, circuit breaker, rate limit, tokens, and estimated cost. That's the story I actually want to tell live. Anything bigger would feel more like an ops dump than a demo.

If this grew into a real production dashboard, I'd split it into a few focused views instead of stuffing everything into one screen.

---

## Tooling note

I used AI assistants for a few mechanical tasks, mainly around synthetic test data and early documentation scaffolding. The architecture decisions, trade-offs, and final implementation choices are my own.
