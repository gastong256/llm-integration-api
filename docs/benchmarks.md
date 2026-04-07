# Benchmarks and observability notes

This is the home for the project's load-testing notes and the matching observability readings.

## What these numbers mean

The useful read in this repo is:
- cache miss vs cache hit
- cache/collapse reuse vs provider-backed infer work
- mixed endpoint share across infer, stream, and classify
- how tokens, cost, and traces move together under a repeatable burst
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

For meaningful Locust runs with the current demo-like profile, raise the limiter first:

```bash
RATE_LIMIT_RPM=90000 make up STACK=obs ARGS="--build -d"
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
| `LOCUST_HOT_INFER_WEIGHT` | `1` | Relative spawn weight for a small repeated infer slice that keeps cache hits visible |
| `LOCUST_ROTATING_INFER_WEIGHT` | `1` | Relative spawn weight for bounded-pool infer prompts that mix hits and misses |
| `LOCUST_UNIQUE_INFER_WEIGHT` | `8` | Relative spawn weight for mostly one-off infer prompts that keep usage and cost moving |
| `LOCUST_STREAM_WEIGHT` | `1` | Relative spawn weight for `/v1/infer/stream` traffic |
| `LOCUST_CLASSIFY_WEIGHT` | `4` | Relative spawn weight for local classify traffic |
| `LOCUST_STREAM_PACING_SECONDS` | `1.25` | Pacing target for the stream user so it stays a smaller slice of the run |

Examples:

Demo-like mixed profile:

```bash
LOCUST_HOT_INFER_WEIGHT=1 LOCUST_ROTATING_INFER_WEIGHT=1 \
LOCUST_UNIQUE_INFER_WEIGHT=8 LOCUST_STREAM_WEIGHT=1 LOCUST_CLASSIFY_WEIGHT=4 \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

Mostly cache-heavy infer profile:

```bash
LOCUST_HOT_INFER_WEIGHT=6 LOCUST_ROTATING_INFER_WEIGHT=1 \
LOCUST_UNIQUE_INFER_WEIGHT=0 LOCUST_STREAM_WEIGHT=0 LOCUST_CLASSIFY_WEIGHT=1 \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

Mostly provider-usage infer profile:

```bash
LOCUST_HOT_INFER_WEIGHT=0 LOCUST_ROTATING_INFER_WEIGHT=1 \
LOCUST_UNIQUE_INFER_WEIGHT=4 LOCUST_STREAM_WEIGHT=0 LOCUST_CLASSIFY_WEIGHT=1 \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

Classify-only mix:

```bash
LOCUST_HOT_INFER_WEIGHT=0 LOCUST_ROTATING_INFER_WEIGHT=0 \
LOCUST_UNIQUE_INFER_WEIGHT=0 LOCUST_STREAM_WEIGHT=0 LOCUST_CLASSIFY_WEIGHT=1 \
make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"
```

## Default benchmark narrative

The default Scene 8 profile is no longer a tiny deterministic prompt loop. It now tries to look more like a real gateway mix:
- a small warm subset that keeps cache-hit behavior visible without dominating the run
- a shared prompt pool that still revisits prompts often enough to show mixed cache behavior
- a unique-prompt subset that keeps provider usage, tokens, and estimated cost moving
- a smaller stream slice so Jaeger keeps showing live SSE traces
- classify traffic so the local model path still appears in the run

The exact percentages drift a little because streaming requests live longer than normal HTTP requests, but the expected read is:
- cache hits remain meaningful but stay closer to half of infer traffic than to a near-total warm-cache run
- misses continue through the full run instead of stopping after the first few requests
- token and cost panels keep moving during the selected range
- stream traces are present without dominating the request mix

## Current benchmark session

The numbers below come from a fresh local session on `2026-04-06` with:
- `RATE_LIMIT_RPM=90000 make up STACK=obs ARGS="--build -d"`
- `LLM_ADAPTER=stub`
- `make bench ARGS="--headless -u 50 -r 10 -t 30s --host http://localhost:8000"`
- the default `scripts/locustfile.py` mix
- three fresh runs, resetting the full stack between runs so Redis started empty each time

### What this profile is trying to simulate

This benchmark is still controlled, but the traffic story is closer to a real app than the earlier tiny prompt loop:
- hot infer prompts stand in for repeated prompt families that should be cheap once warm
- rotating infer prompts stand in for shared business queries that still revisit the same cache keys
- unique infer prompts stand in for one-off prompts that must go all the way to the LLM and generate usage
- stream requests keep SSE traces alive without taking over the whole run
- classify requests keep the local-model path visible

The configured task weights are:
- `hot infer = 1`
- `rotating infer = 1`
- `unique infer = 8`
- `stream = 1`
- `classify = 4`

Because Locust users are paced differently by latency, the realized request mix is not equal to those raw weights. In practice, `classify` ends up taking a larger share of total requests than its user weight suggests because it is much faster than `/v1/infer`, while `stream` remains a small slice because each request stays open longer.

### Approximate expected behavior

Across the three fresh runs, the profile settled into this read:

| Signal | Approximate expectation | Why it looks like that |
| --- | --- | --- |
| `/v1/infer` share of total requests | ~36-37% | infer is still the slowest path, and classify is intentionally weighted higher |
| `/v1/classify` share of total requests | ~63-64% | classify is fast enough to occupy most of the request count in this mix |
| `/v1/infer/stream` share of total requests | ~0.5-0.6% | stream users are fewer and paced more slowly on purpose |
| `cache_hit=true` responses on `/v1/infer` | ~45-50% of infer responses | this includes both warm-cache responses and request-collapsed followers |
| provider-backed infer activity visible through tokens/cost | ~50-55% of infer responses | these are the requests that actually reach the adapter and record usage |

That last pair needs one careful note:
- `cache_hit=true` in this repo does **not** mean only "old warm cache" traffic
- it also includes requests that waited on a shared in-flight leader and then returned through the cache/collapse path
- the provider-backed read comes from token and cost counters rather than the same response counter, so treat both numbers as approximate proportions rather than two exact complementary halves

### Locust results across 3 fresh runs

| Endpoint | Requests | Failures | p50 range | p95 range | Max range | Requests/s range |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `/v1/infer` | 3796-4444 | 0 | 180-210 ms | 400-520 ms | 654.84-726.01 ms | 130.83-153.14 |
| `/v1/classify` | 7158-7193 | 0 | 30-32 ms | 72-73 ms | 116.41-164.47 ms | 246.71-247.90 |
| `/v1/infer/stream` | 64 | 0 | 46-51 ms | 120-150 ms | 216.23-286.67 ms | 2.21 |
| Aggregated | 11018-11676 | 0 | 34-36 ms | 350-420 ms | 654.84-726.01 ms | 379.75-402.37 |

The important read here is:
- infer remains the most expensive path by latency
- classify stays fast and materially contributes to the total throughput
- stream remains deliberately small by request count, but it is present consistently
- the overall run stays stable with no failures once the limiter is raised high enough

### Observability snapshot from the same session

These values came from the live app's `/metrics` endpoint immediately after each run.

| Signal | Range across 3 runs | Read |
| --- | ---: | --- |
| `llm_api_request_latency_seconds_count` | 11407-12038 | total application requests during the run, plus a few collection scrapes |
| `llm_api_cache_hits_total` | 1850-2158 | the cache/collapse path stayed important without taking over the whole infer population |
| `llm_api_tokens_total{type="input"}` | 50520-58185 | unique and leader infer calls kept input-token usage moving strongly through the whole run |
| `llm_api_tokens_total{type="output"}` | 21040-24180 | successful provider-backed infer calls stayed clearly visible in Grafana |
| `llm_api_cost_estimated_usd_total` | 0.020202-0.02323575 USD | estimated LLM cost remained low in absolute terms, but it was clearly non-zero and repeatable |

From Jaeger:
- `inference-api` was present as a traced service during every run
- recent traces included `infer_flow`, `classify_flow`, and `/v1/infer/stream`
- cache/collapse-heavy infer traces often stayed short and returned through `response_build` with `cache.hit=true`
- stream traces kept showing chunk activity plus audit dispatch, which is exactly the point of keeping a small stream slice in the mix

## How to read the results

### Cache matters more than raw infer latency

The biggest signal in this repo is still the gap between:
- a cold `/v1/infer` request
- the same request after the cache is warm

That gap is the easiest thing to confirm both in the API response (`cache_hit`) and in Grafana.

### Mixed workload is not infer-only throughput

The current Locust script mixes:
- `/v1/infer`
- `/v1/infer/stream`
- `/v1/classify`

So these numbers are useful for the repo's demo traffic mix, not as a pure infer benchmark.

### Classify-only is the clean local-model read

The classify-only run is useful because it removes:
- Redis cache effects
- upstream stub latency
- circuit-breaker noise

What remains is the local wrapper and sklearn serving path on its own.

### Stub mode changes the interpretation

With `LLM_ADAPTER=stub`, the upstream is deterministic and local to the app. That's perfect for repeatable resilience and observability demos, but it obviously isn't the same as measuring a real provider over the network.

### Cache hits are broader than pure warm-cache reuse

In this repo, a high `cache_hit=true` share during a concurrent benchmark does not mean every one of those requests reused an old entry from a previous minute. It also includes request-collapsed followers that waited behind a leader and then returned through the cache path.

So the right reading is:
- high `cache_hit=true` share means the app avoided redundant upstream work for many requests
- non-zero token and cost counters mean a smaller but steady subset still reached the adapter and generated usage

## Observability views to pair with a run

### Grafana panels to watch

Keep the dashboard on `Last 15 minutes` or `Last 30 minutes` so the range-based stats retain the run after the burst itself has finished.

Use the dashboard by section rather than panel-by-panel:

- `Range Summary`
  - `Requests in range`
  - `Latency p50 in range`
  - `Latency p95 in range`
  - `Cache hits in range`
  - `429s in range`
  - `Circuit opens in range`
- `Range Usage and Cost`
  - `Input tokens in range`
  - `Output tokens in range`
  - `Estimated cost in range`
- `Live Snapshot`
  - `Live request rate`
  - `Live p95 latency`
  - `Live cache hit rate`
  - `Live provider token throughput`
  - `Live 429 rate`
  - `Live circuit-open rate`
- `Live Trends`
  - `Request rate over time`
  - `Latency over time`
  - `Cache and protection events over time`
  - `Provider usage over time`

The practical reading is:
- range panels tell you what the whole run produced
- live panels tell you whether the burst is peaking, flattening, or degrading right now

### Minimal alerts now included

Prometheus now also loads three intentionally small alert rules:
- `InferenceApiRateLimitActivity`
- `InferenceApiCircuitOpenActivity`
- `InferenceApiHighP95Latency`

These are not meant to page anyone in this repo. Their value is smaller and more practical:
- they prove the metrics are usable for alert conditions, not only dashboards
- they give a clean place to check whether a resilience scene or a bad burst would have crossed an operational threshold
- they stay understandable enough for a demo without dragging in Alertmanager or a larger notification stack

### Prometheus queries that help explain the run

Request volume:

```promql
sum(increase(llm_api_request_latency_seconds_count[15m]))
```

Cache hits:

```promql
sum(increase(llm_api_cache_hits_total[15m]))
```

Rate-limit rejections:

```promql
sum(increase(llm_api_rate_limit_total[15m]))
```

Circuit-open rejections:

```promql
sum(increase(llm_api_circuit_open_total[15m]))
```

Estimated cost in the selected range:

```promql
sum(max_over_time(llm_api_cost_estimated_usd_total[15m]))
```

Input tokens in the selected range:

```promql
sum(max_over_time(llm_api_tokens_total{type="input"}[15m]))
```

Output tokens in the selected range:

```promql
sum(max_over_time(llm_api_tokens_total{type="output"}[15m]))
```

### Jaeger

Jaeger is less about aggregate load and more about explaining one representative request during or after the run.

The most useful traces to compare are:
- an infer response that stays on the cache/collapse path
- an infer response that reaches `llm_call` and contributes usage
- a fast `classify_flow`
- one streaming request with chunk events

For the current mixed run, the clearest proof set is:
- one short infer trace where `cache_check` is tagged with `cache.hit=true`
- one longer infer trace where `llm_call` is present
- one `classify_flow` trace to show the local model path
- one stream trace that includes chunk events plus audit dispatch

## Caveats

- If `RATE_LIMIT_RPM` stays at the default `60`, the limiter dominates the run and the throughput read is mostly useless.
- Warm cache and request collapsing change the shape of the infer distribution quickly.
- The mixed Locust workload is good for the repo's story, but not for capacity claims about infer alone.
- Stream volume is intentionally small by request count; its value is more in traces than in raw throughput.
- If you want more provider usage and less cache/collapse reuse, raise `LOCUST_UNIQUE_INFER_WEIGHT` and lower the hot/rotating weights.
- The classify-only run is a cleaner local-model benchmark, but it still reflects this local machine and container setup.
- Observability numbers need at least one scrape interval to settle in Prometheus/Grafana.
