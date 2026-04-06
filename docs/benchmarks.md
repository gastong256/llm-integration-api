# Benchmarks and observability notes

This is the home for the project's load-testing notes and the matching observability readings.

## What these numbers mean

The useful read in this repo is:
- cache miss vs cache hit
- mixed workload vs infer-heavy workload
- classify-only throughput
- resilience under pressure vs clean scaling

This is not a capacity-planning doc. It's a practical read of how the current project behaves under repeatable local runs.

## Baseline setup

Base stack:

```bash
make up ARGS="--build -d"
```

Observability overlay:

```bash
make up STACK=obs ARGS="--build -d"
```

For meaningful Locust runs, raise the limiter first:

```bash
RATE_LIMIT_RPM=10000 make up STACK=obs ARGS="--build -d"
```

## Locust entrypoint

Baseline mixed run:

```bash
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

The script now accepts these env overrides:

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOCUST_API_KEY` | `test-key-1` | API key used by both Locust users |
| `LOCUST_INFER_MODEL` | `gpt-4o-mini` | Model sent to `/v1/infer` |
| `LOCUST_CLASSIFY_VERSION` | `v1` | Model version sent to `/v1/classify` |
| `LOCUST_INFER_WEIGHT` | `1` | Relative spawn weight for the infer user |
| `LOCUST_CLASSIFY_WEIGHT` | `1` | Relative spawn weight for the classify user |
| `LOCUST_INFER_INPUT_MODE` | `mixed` | `mixed` rotates prompts, `single` repeats one infer request to warm the cache |

Examples:

Infer-heavy mix:

```bash
LOCUST_INFER_WEIGHT=4 LOCUST_CLASSIFY_WEIGHT=1 \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

Classify-heavy mix:

```bash
LOCUST_INFER_WEIGHT=1 LOCUST_CLASSIFY_WEIGHT=4 \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

Warm-cache infer mix:

```bash
LOCUST_INFER_WEIGHT=4 LOCUST_CLASSIFY_WEIGHT=1 LOCUST_INFER_INPUT_MODE=single \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

Classify-only mix:

```bash
LOCUST_INFER_WEIGHT=0 LOCUST_CLASSIFY_WEIGHT=1 \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

## Current benchmark session

The numbers below come from a fresh local session on `2026-04-06` with:
- `RATE_LIMIT_RPM=10000 make up STACK=obs ARGS="--build -d"`
- `LLM_ADAPTER=stub`
- Locust `8` users, spawn rate `2`, runtime `20s`
- three repetitions per load scenario

### Direct request spot-checks

These use direct requests against the live app to isolate the three signals that matter most in this repo.

| Scenario | p50 range | p95 range | Notes |
| --- | ---: | ---: | --- |
| `/v1/infer` cache miss | 154.84-158.48 ms | 159.28-163.93 ms | 3 runs, 5 unique prompts each |
| `/v1/infer` cache hit | 1.84-2.29 ms | 2.39-2.78 ms | 3 runs, 10 repeated requests after warm-up |
| `/v1/classify` | 2.46-2.67 ms | 2.73-3.82 ms | 3 runs, 10 local wrapper requests each |

### Locust results

#### Mixed workload

Default traffic mix from `scripts/locustfile.py`:
- `/v1/infer`
- `/v1/classify`
- 1:1 user weights
- rotating infer prompts

Results across 3 runs:

| Endpoint | Requests | Failures | p50 range | p95 range | Max range |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/v1/infer` | 1388-1400 | 0 | 4-7 ms | 14-21 ms | 67.99-169.08 ms |
| `/v1/classify` | 1396-1400 | 0 | 8-10 ms | 17-20 ms | 63.74-92.27 ms |
| Aggregated | 2784-2800 | 0 | 8-9 ms | 16-20 ms | 67.99-169.08 ms |

Observed throughput:
- ~139-140 req/s aggregate

#### Warm-cache infer-heavy workload

Configured with:
- `LOCUST_INFER_WEIGHT=4`
- `LOCUST_CLASSIFY_WEIGHT=1`
- `LOCUST_INFER_INPUT_MODE=single`

Results across 3 runs:

| Endpoint | Requests | Failures | p50 range | p95 range | Max range |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/v1/infer` | 2117-2120 | 0 | 7-9 ms | 17-20 ms | 61.26-91.91 ms |
| `/v1/classify` | 680-682 | 0 | 7-8 ms | 14-16 ms | 50.43-74.84 ms |
| Aggregated | 2797-2802 | 0 | 7-8 ms | 16-19 ms | 61.26-91.91 ms |

Observed throughput:
- ~140 req/s aggregate

#### Classify-only workload

Configured with:
- `LOCUST_INFER_WEIGHT=0`
- `LOCUST_CLASSIFY_WEIGHT=1`

Results across 3 runs:

| Endpoint | Requests | Failures | p50 range | p95 range | Max range |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/v1/classify` | 2793-2800 | 0 | 13-15 ms | 19-22 ms | 58.02-72.41 ms |
| Aggregated | 2793-2800 | 0 | 13-15 ms | 19-22 ms | 58.02-72.41 ms |

Observed throughput:
- ~140 req/s aggregate

The important read here is:
- the miss/hit gap stays large in direct spot-checks
- once the cache is warm, the mixed and infer-heavy runs stay stable with no failures
- the infer tail tightens when repeated requests stop paying the stub latency cost

### Observability snapshot from the same session

These values came from the live app's `/metrics` endpoint after the repeated benchmark session, plus Jaeger traces from the same overlay run.

From `/metrics`:

| Signal | Value | Read |
| --- | ---: | --- |
| `llm_api_request_latency_seconds_count` | 26486 | tens of thousands of requests served across the repeated session |
| `llm_api_cache_hits_total` | 11078 | cache-hit activity remained high across the infer-heavy and spot-check paths |
| `llm_api_tokens_total{type="input"}` | 82 | provider-side input tokens stayed comparatively low because repeated hits bypassed the stub |
| `llm_api_tokens_total{type="output"}` | 220 | same story on output tokens |
| `llm_api_cost_estimated_usd_total` | 0.0001443 USD | estimated cost stayed near zero even after the repeated session |

From Jaeger:
- `inference-api` was present as a traced service during the run
- recent traces included both `infer_flow` and `classify_flow`
- recent warm-cache traces showed `infer_flow -> cache_check -> GET -> response_build`
- those traces carried `cache.hit=true` on `cache_check` and `response_build`
- representative cache-hit traces did not include `llm_call`, which matches the metrics story

## How to read the results

### Cache matters more than raw infer latency

The biggest signal in this repo is still the gap between:
- a cold `/v1/infer` request
- the same request after the cache is warm

That gap is the easiest thing to confirm both in the API response (`cache_hit`) and in Grafana.

### Mixed workload is not infer-only throughput

The default Locust script mixes:
- `/v1/infer`
- `/v1/classify`

So the numbers are useful for the repo's demo traffic mix, not as a pure infer benchmark.

### Classify-only is the clean local-model read

The classify-only run is useful because it removes:
- Redis cache effects
- upstream stub latency
- circuit-breaker noise

What remains is the local wrapper and sklearn serving path on its own.

### Stub mode changes the interpretation

With `LLM_ADAPTER=stub`, the upstream is deterministic and local to the app. That's perfect for repeatable resilience and observability demos, but it obviously isn't the same as measuring a real provider over the network.

## Observability views to pair with a run

### Grafana panels to watch

During a Locust run, the most useful panels are:
- request rate
- latency p50
- latency p95
- cache hit signal
- rate limit rejections
- circuit breaker opens
- input tokens
- output tokens
- estimated cost total / rate

### Prometheus queries that help explain the run

Request volume:

```promql
sum(rate(llm_api_request_latency_seconds_count[5m]))
```

Estimated cost:

```promql
sum(llm_api_cost_estimated_usd_total)
```

Input tokens:

```promql
sum by (model) (llm_api_tokens_total{type="input"})
```

Output tokens:

```promql
sum by (model) (llm_api_tokens_total{type="output"})
```

### Jaeger

Jaeger is less about aggregate load and more about explaining one representative request during or after the run.

The most useful traces to compare are:
- infer cache miss
- infer cache hit
- a fast circuit-open rejection if you force failures

For the cache-heavy run above, the clearest proof is a trace where:
- `infer_flow` stays short
- `cache_check` is tagged with `cache.hit=true`
- `response_build` is also tagged with `cache.hit=true`
- `llm_call` is absent

For streaming, a cancelled request is still the clearest trace to show because it leaves:
- chunk activity
- cancellation
- audit dispatch

## Caveats

- If `RATE_LIMIT_RPM` stays at the default `60`, the limiter dominates the run and the throughput read is mostly useless.
- Warm cache changes the shape of the infer distribution quickly.
- The mixed Locust workload is good for the repo's story, but not for capacity claims about infer alone.
- The classify-only run is a cleaner local-model benchmark, but it still reflects this local machine and container setup.
- Observability numbers need at least one scrape interval to settle in Prometheus/Grafana.
