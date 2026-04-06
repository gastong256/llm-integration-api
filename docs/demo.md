# Demo guide

This guide covers the live presentation flow for the project.

The main idea is simple: keep the original gateway path intact, then layer observability and the model-serving evolution story on top of it.

It focuses on scene order, transitions, and the observability checkpoints that matter during the walkthrough.

## Start the stack

```bash
make up STACK=obs ARGS="--build -d"
```

Useful URLs:
- app: `http://localhost:8000`
- Jaeger: `http://localhost:16686`
- Grafana: `http://localhost:3000`
- Prometheus: `http://localhost:9090`

Useful log tail:

```bash
make logs STACK=obs ARGS="--no-color --tail=120 app"
```

## Run the demo runner

The runner gives a reproducible walkthrough of the request path, resilience controls, and observability views.

Guided mode:

```bash
make demo
```

Auto mode:

```bash
make demo ARGS="--auto"
```

Single scene:

```bash
make demo ARGS="--auto --scene 4"
```

Supported env overrides:
- `DEMO_BASE_URL`
- `DEMO_JAEGER_URL`
- `DEMO_GRAFANA_URL`
- `DEMO_API_KEY`
- `DEMO_RATE_LIMIT_KEY`

## Scene order

### Cover
- Open with the API name and the gateway summary from the runner.
- Transition: "I'll walk the real request path first, then the resilience and observability pieces around it."

### Scene 1: Architecture
- Show `/health`.
- Point out:
  - `redis`
  - `llm_circuit`
  - `models_loaded`
- Mention Jaeger and Grafana as the two supporting views for the rest of the demo.
- Transition: "With the baseline healthy, I can move into the request path itself."

### Scene 2: Infer happy path
- Show one normal `/v1/infer` response.
- In Jaeger, show the latest infer trace and call out:
  - `infer_flow`
  - `cache_check`
  - `circuit_breaker_check`
  - `llm_call`
  - `cache_set`
  - `response_build`
- In Grafana, show:
  - request rate
  - latency p50
- Show one `infer_complete` log line and point out that the log and trace share:
  - `request_id`
  - `trace_id`
  - `span_id`
- Mention safe observability here:
  - prompt/output previews are bounded
  - obvious secret-like config keys are masked
- Transition: "Now I can repeat the same request and show the cached path."

### Scene 3: Cache hit
- Show the cold call and the warm call.
- Call out:
  - `cache_hit=false` then `cache_hit=true`
  - large latency drop
- In Jaeger, compare the warm path to the miss path and point out the lighter flow.
- In Grafana, show the cache panel and lower latency.
- Transition: "The gateway path is working; next I can switch to the local model-serving path."

### Scene 4: Classify through wrappers
- Show `v1` and `v2` on the same noisy input.
- Mention:
  - the single classify path goes through internal wrappers
  - preprocessing lives with the wrapper, not in the route
  - `v1` and `v2` differ because of unigram vs bigram training
- Optional Jaeger/logs note:
  - `classify_complete` also carries the traced log correlation now
- Transition: "With both serving paths covered, I can move to resilience controls."

### Scene 5: Rate limiting with headers
- Show one successful infer response with:
  - `X-RateLimit-Limit`
  - `X-RateLimit-Remaining`
  - `X-RateLimit-Reset`
- Then show the `429` with:
  - the same rate-limit headers
  - `Retry-After`
- In Grafana, show `Rate limit rejections`.
- Transition: "From client protection, the next step is upstream protection."

### Scene 6: Circuit breaker opening and recovery
- Show the repeated failures while the stub is forced to time out.
- Then show the fast `503` rejection once the circuit is open.
- In Jaeger, point out:
  - a failing infer trace
  - then a fast rejection path when the circuit is already open
- In Grafana, show `Circuit breaker opens`.
- Show the healthy request after recreating the app with a healthy stub again.
- Transition: "The next failure mode is infrastructure degradation under Redis."

### Scene 7: Redis degradation
- Stop Redis and show:
  - `/health` returning `status=degraded`
  - `/v1/infer` failing with `503`
- Mention that `/v1/classify` would still work because it doesn't depend on Redis.
- If you want one observability note here, keep it short:
  - the degraded contract matters more than the dashboard in this scene
- Mention safe observability again if you show logs:
  - even in degraded paths the log-visible payloads stay bounded and masked

## Correlation point to show live

The cleanest place to show log/trace correlation is Scene 2 or Scene 3.

Use:
- one `infer_complete` log line from the app logs
- the matching trace in Jaeger

Call out that:
- `request_id` ties the app log to the HTTP request
- `trace_id` and `span_id` tie the same log line to the Jaeger trace

That makes logs and traces feel like one story instead of two separate tools.

## Close

Suggested close:

"So the project keeps the original gateway behavior while making the request path, resilience controls, classifier boundary, and observability story easy to show in one run."
