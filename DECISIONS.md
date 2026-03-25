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
