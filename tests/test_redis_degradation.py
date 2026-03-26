import pytest
import redis.exceptions
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.health import router as health_router
from app.api.routes.infer import router as infer_router
from app.core.circuit_breaker import CircuitBreaker
from app.core.schemas import InferRequest
from app.services.inference_service import InferenceService


class FailingCache:
    def __init__(self) -> None:
        self.set_attempted = False

    async def get(self, model: str, input: str, config: dict | None) -> None:
        raise redis.exceptions.ConnectionError("redis down")

    async def set(self, model: str, input: str, config: dict | None, value: dict) -> None:
        self.set_attempted = True
        raise redis.exceptions.ConnectionError("redis down")


class FakeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def infer(self, model: str, input: str, config: dict | None) -> dict:
        self.calls += 1
        return {
            "output": f"[stub] {input}",
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 10,
                "total_tokens": 12,
            },
        }


class FailingRateLimiter:
    async def check(self, client_id: str) -> tuple[bool, float]:
        raise redis.exceptions.ConnectionError("redis down")


class UnusedService:
    async def infer(self, request_id: str, req: InferRequest) -> None:
        raise AssertionError("inference service should not be called")


class FailingRedisClient:
    async def ping(self) -> bool:
        raise redis.exceptions.ConnectionError("redis down")


@pytest.mark.asyncio
async def test_inference_service_treats_redis_cache_failures_as_miss() -> None:
    adapter = FakeAdapter()
    cache = FailingCache()
    service = InferenceService(adapter, cache, CircuitBreaker())

    response = await service.infer(
        "req-123",
        InferRequest(model="gpt-4o-mini", input="hello world", config={"temperature": 0.1}),
    )

    assert response.output == "[stub] hello world"
    assert response.cache_hit is False
    assert adapter.calls == 1
    assert cache.set_attempted is True


@pytest.mark.asyncio
async def test_infer_returns_503_when_rate_limiter_storage_is_down() -> None:
    app = FastAPI()
    app.state.rate_limiter = FailingRateLimiter()
    app.state.inference_service = UnusedService()
    app.include_router(infer_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/infer",
            headers={"X-API-Key": "test-key-1"},
            json={"model": "gpt-4o-mini", "input": "hello"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"error": "service temporarily degraded", "retry_after_s": None}
    }


@pytest.mark.asyncio
async def test_health_reports_degraded_when_redis_is_down() -> None:
    app = FastAPI()
    app.state.redis_client = FailingRedisClient()
    app.state.circuit_breaker = CircuitBreaker()
    app.state.models_loaded = []
    app.include_router(health_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "redis": "down",
        "llm_circuit": "closed",
        "models_loaded": [],
    }
