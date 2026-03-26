from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.middleware.auth import require_api_key
from app.core.exceptions import ModelVersionNotFound
from app.core.schemas import ClassifyRequest, ClassifyResponse, ErrorResponse
from app.services.classify_service import ClassifyService

router = APIRouter()


@router.post("/v1/classify", response_model=ClassifyResponse)
async def classify(
    req: ClassifyRequest,
    request: Request,
    _: str = Depends(require_api_key),
    model_version: str = Header(default="v1", alias="X-Model-Version"),
) -> ClassifyResponse:
    service: ClassifyService = request.app.state.classify_service
    try:
        return await service.classify(req.input, model_version)
    except ModelVersionNotFound as exc:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(error=str(exc)).model_dump(),
        )
