import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.middleware.rate_limit import check_rate_limit
from app.core.schemas import InferRequest
from app.services.streaming_service import StreamingService

router = APIRouter()


@router.post("/v1/infer/stream")
async def stream_infer(
    req: InferRequest,
    request: Request,
    _: None = Depends(check_rate_limit),
) -> StreamingResponse:
    request_id: str = structlog.contextvars.get_contextvars().get("request_id", "")
    service: StreamingService = request.app.state.streaming_service
    return StreamingResponse(
        service.stream(request_id, req, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
