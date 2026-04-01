from fastapi import Depends, Request, Response

from app.api.middleware.auth import require_api_key
from app.core.exceptions import RateLimitExceeded


async def check_rate_limit(
    request: Request,
    response: Response,
    client_id: str = Depends(require_api_key),
) -> None:
    allowed, retry_after_s, remaining, reset_s = await request.app.state.rate_limiter.check(
        client_id
    )
    limit = str(request.app.state.rate_limiter.limit)
    headers = {
        "X-RateLimit-Limit": limit,
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset_s),
    }
    if not allowed:
        raise RateLimitExceeded(
            retry_after_s=retry_after_s,
            limit=request.app.state.rate_limiter.limit,
            remaining=remaining,
            reset_s=reset_s,
        )
    response.headers.update(headers)
