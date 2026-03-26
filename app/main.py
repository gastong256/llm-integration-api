from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI

from app.adapters.stub import StubLLMAdapter
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.routes.infer import router as infer_router
from app.core.circuit_breaker import CircuitBreaker
from app.core.settings import get_settings
from app.infra.cache import SemanticCache
from app.infra.rate_limiter import SlidingWindowRateLimiter
from app.services.inference_service import InferenceService

logger = structlog.get_logger()

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    redis_client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    cache = SemanticCache(redis_client, settings.cache_ttl)
    rate_limiter = SlidingWindowRateLimiter(redis_client, settings.rate_limit_rpm)
    circuit_breaker = CircuitBreaker()
    adapter = StubLLMAdapter(failure_rate=settings.stub_failure_rate)

    app.state.rate_limiter = rate_limiter
    app.state.inference_service = InferenceService(adapter, cache, circuit_breaker)

    logger.info("startup", message="AI Inference API starting up")
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
app.include_router(infer_router)
