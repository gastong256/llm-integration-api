import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = structlog.contextvars.get_contextvars()["request_id"]
        return response
