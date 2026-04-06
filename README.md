# llm-integration-api

LLM proxy with Redis-backed caching, sliding-window rate limiting, and a circuit breaker. Includes an embedded scikit-learn classifier and Prometheus metrics.

---

## Quick Start

Docker quick start does not require `make setup`.

```bash
make up          # docker compose up --build (app + redis)
curl http://localhost:8000/health
```

That's it. No API keys, no external services needed. The default adapter is a deterministic stub.

For local development or local test runs, install dependencies first:

```bash
make setup       # install deps via uv
make run         # uvicorn with --reload
```

```bash
make setup
make test
```

Common workflows:

```bash
RATE_LIMIT_RPM=10000 make up STACK=obs ARGS="--build -d"
make logs STACK=obs ARGS="--no-color --tail=200 app"
make demo ARGS="--auto --scene 2"
make bench ARGS="--headless -u 8 -r 2 -t 20s --host http://localhost:8000"
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
  -d '{"model":"gpt-4o-mini","input":"Say hello from the stub adapter again"}'
```

```json
{
  "request_id": "<uuid>",
  "output": "[stub] Say hello from the stub adapter again",
  "model": "gpt-4o-mini",
  "usage": {"tokens_in": 7, "tokens_out": 10},
  "latency_ms": <ms>,
  "cache_hit": false
}
```

**POST /v1/infer/stream**

```bash
curl -N -X POST http://localhost:8000/v1/infer/stream \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","input":"hello"}'
```

```
data: {"token": "token_0", "index": 0}
data: {"token": "token_1", "index": 1}
...
data: [DONE]
```

**POST /v1/classify**

Default `v1`:

```bash
curl -X POST http://localhost:8000/v1/classify \
  -H "X-API-Key: test-key-1" \
  -H "Content-Type: application/json" \
  -d '{"input":"pricing is not accurate"}'
```

```json
{"label":"positive","confidence":<0..1>,"model_version":"v1","latency_ms":<ms>}
```

`v2` with the same input:

```bash
curl -X POST http://localhost:8000/v1/classify \
  -H "X-API-Key: test-key-1" \
  -H "X-Model-Version: v2" \
  -H "Content-Type: application/json" \
  -d '{"input":"pricing is not accurate"}'
```

```json
{"label":"negative","confidence":<0..1>,"model_version":"v2","latency_ms":<ms>}
```

**GET /health**

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "redis": "ok", "llm_circuit": "closed", "models_loaded": ["v1", "v2"]}
```

**GET /metrics**

```bash
curl http://localhost:8000/metrics
```

Returns Prometheus text format. Tracked: request latency histogram, cache hits, rate limit rejections, circuit-open rejections.
This endpoint is exposed at runtime even though it is not part of the app's OpenAPI schema.

---

## Manual verification

For a focused manual validation flow covering the critical challenge behaviors — auth, infer, stream, classify, cache hits, rate limiting, circuit breaker, and degraded Redis mode — see [docs/manual-checks.md](docs/manual-checks.md).

---

## Local observability and walkthrough

The project also includes an observability overlay and a guided walkthrough around the base gateway path.

### Observability overlay

Bring the full demo stack up with:

```bash
make up STACK=obs ARGS="--build -d"
```

Useful URLs:
- app: `http://localhost:8000`
- Jaeger: `http://localhost:16686`
- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`

Tracing stays opt-in. The base stack still works on its own; the overlay just turns on the extra observability path by setting:
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`
- `OTEL_SERVICE_NAME=inference-api`

### Guided demo runner

The live demo flow is scripted in `scripts/demo.py`.

```bash
make demo
```

Helpful variants:

```bash
make demo ARGS="--auto"
make demo ARGS="--auto --scene 3"
```

The full scene order and the live presentation notes are in [docs/demo.md](docs/demo.md).

### Internal model boundary

`/v1/classify` now runs through a small internal wrapper contract in `sdk/`, with the concrete wrappers kept in `app/models/`. That keeps the SDK real but still repo-local, and it means the running app uses the same boundary the code is describing.

### Estimated cost metrics

The app now emits:
- input token counters
- output token counters
- `estimated cost` counters

Cost is derived from post-flight `usage` plus configured pricing. It is useful operationally, but it is only an estimate.

### Logs and traces

Structured logs still carry `request_id`, and traced requests now also carry `trace_id` and `span_id`. This makes log-to-trace correlation explicit in the walkthrough and in local debugging.

Prompt and output previews in demo-visible logs are bounded on purpose, and obvious secret-bearing keys like `api_key`, `token`, and `authorization` are masked in log-visible payloads.

### gRPC evolution artifact

There is no runnable gRPC server in the current project. The `.proto` file in `protos/model_serving.proto` documents a possible internal evolution toward an HTTP edge -> gRPC model-serving split.

---

## Configuration

All settings are env vars with defaults that work out of the box for local runs. Docker Compose injects its own container-specific `REDIS_URL`.

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis URL for local runs |
| `API_KEYS` | `test-key-1,test-key-2` | Comma-separated valid API keys |
| `RATE_LIMIT_RPM` | `60` | Max requests per minute per API key |
| `CACHE_TTL` | `300` | Cache TTL in seconds |
| `LLM_ADAPTER` | `stub` | `stub` for zero-config local/dev, `http` for a real OpenAI-compatible backend |
| `LLM_BASE_URL` | `http://localhost:11434` | Base URL for HTTP adapter (OpenAI-compatible) |
| `LLM_TIMEOUT` | `30` | HTTP timeout in seconds |
| `LLM_API_KEY` | — | Optional bearer token for the HTTP adapter |
| `STUB_FAILURE_RATE` | `0.0` | Stub-only failure rate. `1.0` makes every stub call time out |
| `LLM_PRICE_INPUT_PER_1K_TOKENS_USD` | `0.00015` | Estimated-cost input token pricing |
| `LLM_PRICE_OUTPUT_PER_1K_TOKENS_USD` | `0.0006` | Estimated-cost output token pricing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | Optional OTLP gRPC endpoint. Needed only for the observability overlay |
| `OTEL_SERVICE_NAME` | `inference-api` | Service name reported to OpenTelemetry when tracing is enabled |

Copy `.env.example` to `.env` to override locally.

Load-test-only env vars stay local to `scripts/locustfile.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCUST_API_KEY` | `test-key-1` | API key used by the Locust users |
| `LOCUST_INFER_MODEL` | `gpt-4o-mini` | Model sent to `/v1/infer` during load runs |
| `LOCUST_CLASSIFY_VERSION` | `v1` | `X-Model-Version` sent to `/v1/classify` during load runs |

Demo-runner-only env vars stay local to `scripts/demo.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_BASE_URL` | `http://localhost:8000` | Base URL used by the guided demo runner |
| `DEMO_JAEGER_URL` | `http://localhost:16686` | Jaeger URL used by the guided demo runner |
| `DEMO_GRAFANA_URL` | `http://localhost:3000` | Grafana URL used by the guided demo runner |
| `DEMO_API_KEY` | `test-key-1` | Primary API key used during the guided demo |
| `DEMO_RATE_LIMIT_KEY` | `test-key-2` | Separate API key used in the rate-limit scene |

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
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

The default `RATE_LIMIT_RPM=60` will dominate results before you see anything useful about throughput. For real load tests, raise it:

```bash
RATE_LIMIT_RPM=10000 make up ARGS="--build -d"
```

Then run Locust in a second terminal. The current `scripts/locustfile.py` mixes `/v1/infer` and `/v1/classify`, so these runs reflect the repo's demo traffic mix rather than infer-only throughput. If you want to pair the run with Jaeger and Grafana, lift the observability overlay instead:

```bash
RATE_LIMIT_RPM=10000 make up STACK=obs ARGS="--build -d"
```

In local runs, the main signal is still the gap between an infer cache miss and a warm cache hit. The benchmark doc keeps fresh sample numbers and the matching observability readings so the README can stay short.

Supported Locust overrides:
- `LOCUST_API_KEY`
- `LOCUST_INFER_MODEL`
- `LOCUST_CLASSIFY_VERSION`
- `LOCUST_INFER_WEIGHT`
- `LOCUST_CLASSIFY_WEIGHT`
- `LOCUST_INFER_INPUT_MODE`

For the repeated mixed, warm-cache, and classify-only runs, plus the matching Grafana / Prometheus / Jaeger readings, see [docs/benchmarks.md](docs/benchmarks.md).

---

## Models

`models/v1.joblib` and `models/v2.joblib` are pre-trained scikit-learn pipelines (TF-IDF + logistic regression) committed to the repo. They were trained on small retail-observation sentiment examples so the classify demo stays closer to the company domain. `make up` works without any extra steps.

To regenerate the models from scratch:

```bash
uv run python scripts/train_models.py
```

---

## Design Notes

Architecture decisions, trade-off rationale, and load scenario documentation are in [DECISIONS.md](DECISIONS.md).

## Tooling note

I used AI assistants for a few mechanical tasks, mainly around synthetic test data and early documentation scaffolding. The architecture decisions, trade-offs, and final implementation choices are my own.

---

## Release

Challenge submitted under tag `v1.0.0` on 2026-03-27.
