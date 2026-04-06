# Benchmarks and observability notes

This is the home for the branch's load-testing notes and the matching observability readings.

I didn't want this detail sitting in `DECISIONS.md`, and it was starting to make the README too heavy too. So the README keeps the entrypoint, and this doc keeps the measurement details.

## What these numbers mean

The useful read in this repo is:
- cache miss vs cache hit
- mixed workload vs infer-heavy workload
- resilience under pressure vs clean scaling

This is not a capacity-planning doc. It's a practical read of how the current branch behaves under repeatable local runs.

## Baseline setup

Base stack:

```bash
docker compose up --build -d
```

Observability overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
```

For meaningful Locust runs, raise the limiter first:

```bash
RATE_LIMIT_RPM=10000 docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
```

## Locust entrypoint

Baseline mixed run:

```bash
uv run locust -f scripts/locustfile.py --headless -u 50 -r 10 -t 30s \
  --host http://localhost:8000
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
uv run locust -f scripts/locustfile.py --headless -u 50 -r 10 -t 30s \
  --host http://localhost:8000
```

Classify-heavy mix:

```bash
LOCUST_INFER_WEIGHT=1 LOCUST_CLASSIFY_WEIGHT=4 \
uv run locust -f scripts/locustfile.py --headless -u 50 -r 10 -t 30s \
  --host http://localhost:8000
```

Warm-cache infer mix:

```bash
LOCUST_INFER_WEIGHT=4 LOCUST_CLASSIFY_WEIGHT=1 LOCUST_INFER_INPUT_MODE=single \
uv run locust -f scripts/locustfile.py --headless -u 50 -r 10 -t 30s \
  --host http://localhost:8000
```

## Current benchmark session

The numbers below come from a fresh local run on `2026-04-05` with:
- `docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d`
- `RATE_LIMIT_RPM=10000`
- `LLM_ADAPTER=stub`
- Locust `8` users, spawn rate `2`, runtime `20s`

### Direct request spot-checks

These use direct requests against the live app to isolate the three signals that matter most in this repo.

| Scenario | p50 | p95 | Notes |
| --- | ---: | ---: | --- |
| `/v1/infer` cache miss | 156.51 ms | 159.69 ms | 5 unique prompts |
| `/v1/infer` cache hit | 1.97 ms | 3.33 ms | 10 repeated requests after one warm-up call |
| `/v1/classify` | 2.48 ms | 3.06 ms | 10 local wrapper requests |

### Locust results

#### Mixed workload

Default traffic mix from `scripts/locustfile.py`:
- `/v1/infer`
- `/v1/classify`
- 1:1 user weights
- rotating infer prompts

Results:

| Endpoint | Requests | Failures | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/v1/infer` | 1456 | 0 | 7 ms | 18 ms | 163 ms |
| `/v1/classify` | 1465 | 0 | 9 ms | 13 ms | 71 ms |
| Aggregated | 2921 | 0 | 8 ms | 17 ms | 163 ms |

Observed throughput:
- ~147 req/s aggregate

#### Warm-cache infer-heavy workload

Configured with:
- `LOCUST_INFER_WEIGHT=4`
- `LOCUST_CLASSIFY_WEIGHT=1`
- `LOCUST_INFER_INPUT_MODE=single`

Results:

| Endpoint | Requests | Failures | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/v1/infer` | 2213 | 0 | 7 ms | 15 ms | 80 ms |
| `/v1/classify` | 712 | 0 | 7 ms | 12 ms | 59 ms |
| Aggregated | 2925 | 0 | 7 ms | 14 ms | 80 ms |

Observed throughput:
- ~148 req/s aggregate

The useful read here is not "warm cache makes Locust infinitely fast". It is:
- the miss/hit gap stays large in direct spot-checks
- once the cache is warm, the mixed and infer-heavy runs stay stable with no failures
- the infer tail tightens when repeated requests stop paying the stub latency cost

### Observability snapshot from the same session

These values came from the live app's `/metrics` endpoint after the benchmark session, plus Jaeger traces from the same overlay run.

From `/metrics`:

| Signal | Value | Read |
| --- | ---: | --- |
| `llm_api_request_latency_seconds_count` | 5956 | thousands of requests served in one short local session |
| `llm_api_cache_hits_total` | 3747 | cache hit activity dominated the session |
| `llm_api_tokens_total{type="input"}` | 49 | provider-side input tokens stayed low because repeated hits bypassed the stub |
| `llm_api_tokens_total{type="output"}` | 110 | same story on output tokens |
| `llm_api_cost_estimated_usd_total` | 0.00007335 USD | estimated cost stayed near zero under cache-heavy traffic |

From Jaeger:
- `inference-api` was present as a traced service during the run
- recent warm-cache traces showed `infer_flow -> cache_check -> GET -> response_build`
- those traces carried `cache.hit=true` on `cache_check` and `response_build`
- the representative cache-hit traces did not include `llm_call`, which matches the metrics story

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
- Observability numbers need at least one scrape interval to settle in Prometheus/Grafana.
