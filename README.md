# llm-integration-api

LLM proxy with Redis-backed caching, sliding-window rate limiting, and a circuit breaker. Includes an embedded scikit-learn classifier and Prometheus metrics.

---

## Quick Start

```bash
make up          # docker-compose up --build (app + redis)
curl http://localhost:8000/health
```

That's it. No API keys, no external services needed. The default adapter is a deterministic stub.

For local development without Docker:

```bash
make setup       # install deps via uv
make run         # uvicorn with --reload
```

---

## Architecture

```
Client
  │
  ▼
[FastAPI + Middleware]   ← auth (X-API-Key), rate limit (Redis), request-id
  │
  ├── POST /v1/infer ────────→ InferenceService
  │                                  │
  │                          ┌───────┴────────┐
  │                          ▼                ▼
  │                     Cache (Redis)   Circuit Breaker
  │                          │                │
  │                          └───────┬────────┘
  │                                  ▼
  │                          LLM Adapter (stub / http)
  │
  ├── POST /v1/infer/stream ─→ StreamingService → SSE chunks + circuit check
  │
  ├── POST /v1/classify ─────→ ClassifyService → ModelRegistry (sklearn)
  │
  ├── GET /health ───────────→ Redis ping + CB state + loaded model versions
  │
  └── GET /metrics ──────────→ Prometheus counters + latency histogram
```

---

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/infer` | ✓ | Single inference, cached, rate-limited |
| POST | `/v1/infer/stream` | ✓ | SSE streaming inference with circuit breaker |
| POST | `/v1/classify` | ✓ | Text classification with embedded ML model |
| GET | `/health` | — | Service status (Redis, circuit, models) |
| GET | `/metrics` | — | Prometheus metrics |

---

## Usage Examples

**POST /v1/infer**

```bash
curl -X POST http://localhost:8000/v1/infer \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello","config":{"temperature":0.7}}'
```

```json
{
  "request_id": "a1b2c3...",
  "output": "[stub] hello",
  "model": "gpt-4o",
  "usage": {"tokens_in": 1, "tokens_out": 10},
  "latency_ms": 152.4,
  "cache_hit": false
}
```

**POST /v1/infer/stream**

```bash
curl -N -X POST http://localhost:8000/v1/infer/stream \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","input":"hello"}'
```

```
data: {"token": "token_0", "index": 0}
data: {"token": "token_1", "index": 1}
...
data: [DONE]
```

**POST /v1/classify**

```bash
curl -X POST http://localhost:8000/v1/classify \
  -H "X-API-Key: test-key-1" \
  -H "X-Model-Version: v2" \
  -H "Content-Type: application/json" \
  -d '{"input":"great product, love it"}'
```

```json
{"label": "positive", "confidence": 0.91, "model_version": "v2", "latency_ms": 3.2}
```

**GET /health**

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "redis": "up", "llm_circuit": "closed", "models_loaded": ["v1", "v2"]}
```

**GET /metrics**

```bash
curl http://localhost:8000/metrics
```

Returns Prometheus text format. Tracked: request latency histogram, cache hits, rate limit rejections, circuit-open rejections.

---

## Configuration

All settings are env vars with defaults that work out of the box.

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `API_KEYS` | `test-key-1,test-key-2` | Comma-separated valid API keys |
| `RATE_LIMIT_RPM` | `60` | Max requests per minute per API key |
| `CACHE_TTL` | `300` | Cache TTL in seconds |
| `LLM_ADAPTER` | `stub` | `stub` or `http` |
| `LLM_BASE_URL` | `http://localhost:11434` | Base URL for HTTP adapter (OpenAI-compatible) |
| `LLM_TIMEOUT` | `30` | HTTP timeout in seconds |
| `LLM_API_KEY` | — | Bearer token for HTTP adapter (optional) |
| `STUB_FAILURE_RATE` | `0.0` | Fraction of stub calls that raise TimeoutError (0.0–1.0) |

Copy `.env.example` to `.env` to override locally.

---

## Testing

```bash
make test
```

Covers: infer success + cache hit + 401/429/503 paths, circuit breaker state transitions, SSE streaming format, classify v1/v2/invalid-version, health endpoint, Redis degradation (cache failures, limiter down), thundering herd collapsing, and model registry loading.

```bash
make lint        # ruff check
make format      # ruff format
make precommit   # all pre-commit hooks
```

---

## Load Testing

```bash
uv run locust -f scripts/locustfile.py --headless -u 50 -r 10 -t 30s \
  --host http://localhost:8000
```

The default `RATE_LIMIT_RPM=60` will dominate results before you see anything useful about throughput. For real load tests, raise it:

```bash
RATE_LIMIT_RPM=10000 make run
```

Then run Locust in a second terminal. In production, use dedicated load-test API keys with relaxed limits rather than touching the global default.

---

## Models

`models/v1.joblib` and `models/v2.joblib` are pre-trained scikit-learn pipelines (TF-IDF + logistic regression) committed to the repo. `docker-compose up` works without any extra steps.

To regenerate the models from scratch:

```bash
uv run python scripts/train_models.py
```

---

## Design Notes

Architecture decisions, trade-off rationale, and load scenario documentation are in [DECISIONS.md](DECISIONS.md).
