from fastapi import Depends, HTTPException, Request

from app.api.middleware.auth import require_api_key
from app.core.schemas import ErrorResponse


async def check_rate_limit(
    request: Request,
    client_id: str = Depends(require_api_key),
) -> None:
    allowed, retry_after_s = await request.app.state.rate_limiter.check(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=ErrorResponse(
                error="rate limit exceeded", retry_after_s=retry_after_s
            ).model_dump(),
            headers={"Retry-After": str(int(retry_after_s))},
        )
