import asyncio
import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.responses import Response

from app.adapters.stub import StubLLMAdapter
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.classify import router as classify_router
from app.api.routes.health import router as health_router
from app.api.routes.infer import router as infer_router
from app.api.routes.stream import router as stream_router
from app.core.circuit_breaker import CircuitBreaker
from app.core.model_registry import ModelRegistry
from app.core.settings import Settings, get_settings
from app.infra.cache import SemanticCache
from app.infra.rate_limiter import SlidingWindowRateLimiter
from app.services.classify_service import ClassifyService
from app.services.inference_service import InferenceService
from app.services.streaming_service import StreamingService

logger = structlog.get_logger()

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
REQUEST_LATENCY = Histogram(
    "llm_api_request_latency_seconds",
    "Application request latency in seconds.",
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)
CACHE_HITS = Counter(
    "llm_api_cache_hits_total",
    "Successful infer responses served from cache.",
)
RATE_LIMITS = Counter(
    "llm_api_rate_limit_total",
    "Requests rejected by rate limiting.",
)
CIRCUIT_OPENS = Counter(
    "llm_api_circuit_open_total",
    "Requests rejected because the circuit breaker was open.",
)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


async def _build_redis_dependencies(
    settings: Settings,
) -> tuple[aioredis.Redis, SemanticCache, SlidingWindowRateLimiter]:
    redis_client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = SemanticCache(redis_client, settings.cache_ttl)
    rate_limiter = SlidingWindowRateLimiter(redis_client, settings.rate_limit_rpm)
    return redis_client, cache, rate_limiter


async def _read_response_body(response: Response) -> tuple[bytes, Response]:
    body = b"".join([chunk async for chunk in response.body_iterator])
    rebuilt = Response(
        content=body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
        background=response.background,
    )
    return body, rebuilt


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    circuit_breaker = CircuitBreaker()
    adapter = StubLLMAdapter(failure_rate=settings.stub_failure_rate)
    model_registry = ModelRegistry(MODELS_DIR / "v1.joblib", MODELS_DIR / "v2.joblib")
    # joblib.load is blocking, so model loading goes through to_thread inside the registry.
    redis_resources, loaded_versions = await asyncio.gather(
        _build_redis_dependencies(settings),
        model_registry.load_all(),
    )
    redis_client, cache, rate_limiter = redis_resources

    app.state.redis_client = redis_client
    app.state.rate_limiter = rate_limiter
    app.state.circuit_breaker = circuit_breaker
    app.state.models_loaded = loaded_versions
    app.state.model_registry = model_registry
    app.state.classify_service = ClassifyService(model_registry)
    app.state.inference_service = InferenceService(adapter, cache, circuit_breaker)
    app.state.streaming_service = StreamingService(adapter, circuit_breaker)

    logger.info("startup", message="AI Inference API starting up", models_loaded=loaded_versions)
    yield

    await redis_client.aclose()
    logger.info("shutdown", message="AI Inference API shutting down")


app = FastAPI(
    title="AI Inference API",
    description="Scalable LLM inference with caching, rate limiting, and circuit breaking.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def record_metrics(request, call_next) -> Response:
    started_at = time.perf_counter()
    response = await call_next(request)
    path = request.url.path

    if (
        path == "/v1/infer"
        and response.headers.get("content-type", "").startswith("application/json")
    ):
        body, response = await _read_response_body(response)
        payload = json.loads(body)
        if payload.get("cache_hit") is True:
            CACHE_HITS.inc()
        detail = payload.get("detail")
        if response.status_code == 503 and isinstance(detail, dict):
            if detail.get("error") == "service unavailable":
                CIRCUIT_OPENS.inc()

    if path != "/metrics":
        REQUEST_LATENCY.observe(time.perf_counter() - started_at)
        if response.status_code == 429:
            RATE_LIMITS.inc()

    return response


app.add_middleware(RequestContextMiddleware)
app.include_router(classify_router)
app.include_router(health_router)
app.include_router(infer_router)
app.include_router(stream_router)
Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app, include_in_schema=False)
