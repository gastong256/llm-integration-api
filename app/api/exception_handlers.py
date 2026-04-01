import redis.exceptions
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    CircuitOpenError,
    ModelVersionNotFound,
    RateLimitExceeded,
    UpstreamProviderError,
)
from app.core.schemas import ErrorResponse

logger = structlog.get_logger()


def _log_exception(
    request: Request,
    exc: Exception,
    status_code: int,
    *,
    level: str = "warning",
) -> None:
    log = getattr(logger, level)
    log(
        "request_error",
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        error_type=exc.__class__.__name__,
        error=str(exc),
        request_id=structlog.contextvars.get_contextvars().get("request_id"),
    )


def _error_response(
    status_code: int,
    error: str,
    *,
    retry_after_s: float | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": ErrorResponse(error=error, retry_after_s=retry_after_s).model_dump()},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RateLimitExceeded)
    async def handle_rate_limit_exceeded(
        request: Request, exc: RateLimitExceeded
    ) -> JSONResponse:
        _log_exception(request, exc, 429)
        return _error_response(
            429,
            "rate limit exceeded",
            retry_after_s=exc.retry_after_s,
            headers={
                "Retry-After": str(int(exc.retry_after_s)),
                "X-RateLimit-Limit": str(exc.limit),
                "X-RateLimit-Remaining": str(exc.remaining),
                "X-RateLimit-Reset": str(exc.reset_s),
            },
        )

    @app.exception_handler(CircuitOpenError)
    async def handle_circuit_open_error(
        request: Request, exc: CircuitOpenError
    ) -> JSONResponse:
        _log_exception(request, exc, 503)
        return _error_response(
            503,
            "service unavailable",
            retry_after_s=exc.retry_after_s,
            headers={"Retry-After": str(int(exc.retry_after_s))},
        )

    @app.exception_handler(ModelVersionNotFound)
    async def handle_model_version_not_found(
        request: Request, exc: ModelVersionNotFound
    ) -> JSONResponse:
        _log_exception(request, exc, 400)
        return _error_response(400, str(exc))

    @app.exception_handler(TimeoutError)
    async def handle_timeout_error(request: Request, exc: TimeoutError) -> JSONResponse:
        _log_exception(request, exc, 504)
        return _error_response(504, "LLM provider timeout")

    @app.exception_handler(UpstreamProviderError)
    async def handle_upstream_provider_error(
        request: Request, exc: UpstreamProviderError
    ) -> JSONResponse:
        _log_exception(request, exc, 502)
        return _error_response(502, "upstream provider error")

    @app.exception_handler(redis.exceptions.ConnectionError)
    async def handle_redis_connection_error(
        request: Request, exc: redis.exceptions.ConnectionError
    ) -> JSONResponse:
        _log_exception(request, exc, 503)
        return _error_response(503, "service temporarily degraded")

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        _log_exception(request, exc, 422)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        _log_exception(request, exc, exc.status_code)
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        _log_exception(request, exc, 500, level="exception")
        return _error_response(500, "internal server error")
