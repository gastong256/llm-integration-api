# llm-integration-api

FastAPI service exposing LLMs to external clients with caching, rate limiting, and a circuit breaker.

## Quick start

```bash
make setup       # install deps, generate uv.lock
make up          # docker-compose up --build (app + redis)
curl localhost:8000/health
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/infer` | Single inference request |
| POST | `/v1/infer/stream` | SSE streaming inference |
| POST | `/v1/classify` | ML text classification |
| GET | `/health` | Health check (no auth) |

## Auth

Pass `X-API-Key: test-key-1` (or any key from `API_KEYS` env var).

## Env vars

See [`.env.example`](.env.example).

---

Full docs, architecture diagram and design notes in [DECISIONS.md](DECISIONS.md).
