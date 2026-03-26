from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.middleware.request_context import RequestContextMiddleware

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
    logger.info("startup", message="AI Inference API starting up")
    yield
    logger.info("shutdown", message="AI Inference API shutting down")


app = FastAPI(
    title="AI Inference API",
    description="Scalable LLM inference with caching, rate limiting, and circuit breaking.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
