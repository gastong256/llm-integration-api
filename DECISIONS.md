# DECISIONS.md

Running log of decisions I'm making as I build this. Will clean up and expand before the final polish pass.

---

## 1. Architecture basics

**FastAPI + uvicorn**

Went with FastAPI because I need async natively — LLM calls are pure I/O and blocking them on a thread pool would be wasteful. Also gets me Pydantic v2 and OpenAPI docs for free.

- Considered bare Starlette but FastAPI's DI system pays for the thin wrapper
- Flask was never really on the table for this load profile

**Service layer**

Keeping business logic out of routers. Routes stay thin: extract inputs, call service, return response. Mainly doing this so I can unit-test services without an HTTP client. The inference service will also be called from both sync and streaming routes, so it needs to be reusable anyway.

**Adapter pattern for LLMs**

ABC base + stub impl for now, HTTP adapter coming later. `LLM_ADAPTER` env var picks one at startup.
I don't want this to require real API keys to run — stub default means `docker-compose up` just works.
TODO: expand trade-offs once the HTTP adapter is wired up

**pydantic-settings**

All config in one `Settings` class, validated at startup. Bad env var → crash early with a clear message, not a silent wrong value at runtime. `get_settings()` cached with `lru_cache`, injected via `Depends()`.

**structlog + JSONRenderer**

JSON from day one. `merge_contextvars` lets me bind `request_id` in middleware and have it appear in every log line within that request without threading it through manually. stdlib logging with a JSON formatter would work but doesn't give me that for free.

TODO: figure out dev-friendly console output vs JSON-only — maybe a `LOG_FORMAT=console` flag

---

## 2. Caching and rate limiting

**Sliding window, not fixed window**

The simple alternative is `INCR + EXPIRE` — one key per client, increment on each request, expire after 60 seconds. Two Redis ops, trivial to understand. The problem is it resets at a fixed clock boundary, so a client can exhaust the limit at :59 and immediately exhaust it again at :00, getting 2× the allowed burst in two consecutive seconds.

Sorted set + `ZREMRANGEBYSCORE` fixes this. Every `check()` call evicts entries older than `now - 60` before counting, so the window always reflects the actual last 60 seconds regardless of what the clock says. More ops per request (5 vs 2), but the correctness guarantee is worth it for an API with strict SLAs.

One deliberate choice: rejected requests don't count toward quota (`ZREM` on denial). A hammering client gets a stable `retry_after_s` based on when real allowed requests expire — not an ever-growing penalty from their own rejected calls.

**SHA-256 content-addressed cache keys**

Cache key is SHA-256 of `json.dumps({"model": ..., "input": ..., "config": ...}, sort_keys=True)`. The `sort_keys=True` means `{"temp": 0.5, "max": 100}` and `{"max": 100, "temp": 0.5}` are treated as the same request and hit the same entry. Simple string concatenation would miss this and cause redundant LLM calls for identical requests.

SHA-256 over a shorter hash because a false cache hit (serving the wrong response) is a correctness bug, not a performance miss. The collision probability of SHA-256 in this context is negligible.

**SHA-256 over embeddings**

"SemanticCache" as a name implies vector similarity to most readers — cache near-identical queries, not just exact ones. I went with exact-match instead. Two reasons: the challenge defines the cache key as model + input + config, so fuzzy matching is out of scope by design. And adding an embedding model means a vector DB or an embedding API call on every request path — a whole dependency stack for zero benefit given the stated requirements. SHA-256 of the normalized payload is the right call here.
