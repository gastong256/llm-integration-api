import redis.exceptions
from fastapi import APIRouter, Request

from app.core.circuit_breaker import CircuitBreaker
from app.core.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    redis_status = "ok"
    status = "ok"

    try:
        await request.app.state.redis_client.ping()
    except redis.exceptions.ConnectionError:
        redis_status = "down"
        status = "degraded"

    circuit_breaker: CircuitBreaker = request.app.state.circuit_breaker
    await circuit_breaker.is_open()

    return HealthResponse(
        status=status,
        redis=redis_status,
        llm_circuit=circuit_breaker.state.value,
        models_loaded=list(request.app.state.models_loaded),
    )
