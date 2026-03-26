import redis.exceptions
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.middleware.rate_limit import check_rate_limit
from app.core.exceptions import CircuitOpenError
from app.core.schemas import ErrorResponse, InferRequest, InferResponse
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
    try:
        return await service.infer(request_id, req)
    except redis.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(error="service temporarily degraded").model_dump(),
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=ErrorResponse(error="LLM provider timeout").model_dump(),
        )
    except CircuitOpenError as exc:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                error="service unavailable", retry_after_s=exc.retry_after_s
            ).model_dump(),
            headers={"Retry-After": str(exc.retry_after_s)},
        )
