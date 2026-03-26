import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI

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
    app.state.models_loaded = []
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

app.add_middleware(RequestContextMiddleware)
app.include_router(classify_router)
app.include_router(health_router)
app.include_router(infer_router)
app.include_router(stream_router)
