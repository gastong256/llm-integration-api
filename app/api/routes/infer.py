import structlog
from fastapi import APIRouter, Depends, Request

from app.api.middleware.rate_limit import check_rate_limit
from app.core.schemas import InferRequest, InferResponse
from app.services.inference_service import InferenceService

router = APIRouter()


@router.post("/v1/infer", response_model=InferResponse)
async def infer(
    req: InferRequest,
    request: Request,
    _: None = Depends(check_rate_limit),
) -> InferResponse:
    request_id: str = structlog.contextvars.get_contextvars().get("request_id", "")
    service: InferenceService = request.app.state.inference_service
    return await service.infer(request_id, req)
