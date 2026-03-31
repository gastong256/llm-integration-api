import redis.exceptions
import structlog
from fastapi import Depends, HTTPException, Request, Response

from app.api.middleware.auth import require_api_key
from app.core.schemas import ErrorResponse


async def check_rate_limit(
    request: Request,
    response: Response,
    client_id: str = Depends(require_api_key),
) -> None:
    structlog.contextvars.bind_contextvars(client_id=client_id)
    try:
        allowed, retry_after_s, remaining, reset_s = await request.app.state.rate_limiter.check(
            client_id
        )
    except redis.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(error="service temporarily degraded").model_dump(),
        )
    limit = str(request.app.state.rate_limiter.limit)
    headers = {
        "X-RateLimit-Limit": limit,
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset_s),
    }
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=ErrorResponse(
                error="rate limit exceeded", retry_after_s=retry_after_s
            ).model_dump(),
            headers={
                "Retry-After": str(int(retry_after_s)),
                **headers,
            },
        )
    response.headers.update(headers)
