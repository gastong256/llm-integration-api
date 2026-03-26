from typing import Any

import pytest

from app.core.circuit_breaker import CircuitBreaker
from app.services.inference_service import InferenceService


class InMemoryCache:
    def __init__(self) -> None:
        self._values: dict[str, dict[str, Any]] = {}

    def _make_key(self, model: str, input: str, config: dict[str, Any] | None) -> str:
        return repr((model, input, config))

    async def get(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return self._values.get(self._make_key(model, input, config))

    async def set(
        self,
        model: str,
        input: str,
        config: dict[str, Any] | None,
        value: dict[str, Any],
    ) -> None:
        self._values[self._make_key(model, input, config)] = value


class AllowingRateLimiter:
    async def check(self, client_id: str) -> tuple[bool, float]:
        return True, 0.0


class RejectingRateLimiter:
    async def check(self, client_id: str) -> tuple[bool, float]:
        return False, 12.4


class UnusedInferenceService:
    async def infer(self, request_id: str, req) -> None:
        raise AssertionError("inference service should not be called")


@pytest.mark.asyncio
async def test_infer_returns_success(
    async_client, app_instance, valid_headers, stub_adapter
) -> None:
    app_instance.state.rate_limiter = AllowingRateLimiter()
    app_instance.state.inference_service = InferenceService(
        stub_adapter,
        InMemoryCache(),
        CircuitBreaker(),
    )

    response = await async_client.post(
        "/v1/infer",
        headers=valid_headers,
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["output"] == "[stub] hello world"
    assert body["model"] == "gpt-4o-mini"
    assert body["cache_hit"] is False
    assert body["usage"]["total_tokens"] == 12
    assert body["request_id"]


@pytest.mark.asyncio
async def test_infer_returns_cache_hit_on_second_request(
    async_client, app_instance, valid_headers, stub_adapter
) -> None:
    app_instance.state.rate_limiter = AllowingRateLimiter()
    app_instance.state.inference_service = InferenceService(
        stub_adapter,
        InMemoryCache(),
        CircuitBreaker(),
    )
    payload = {"model": "gpt-4o-mini", "input": "same request", "config": {"temperature": 0.1}}

    first_response = await async_client.post("/v1/infer", headers=valid_headers, json=payload)
    second_response = await async_client.post("/v1/infer", headers=valid_headers, json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["cache_hit"] is False
    assert second_response.json()["cache_hit"] is True
    assert stub_adapter.calls == 1


@pytest.mark.asyncio
async def test_infer_requires_api_key(async_client) -> None:
    response = await async_client.post(
        "/v1/infer",
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid or missing API key"}


@pytest.mark.asyncio
async def test_infer_returns_429_when_rate_limit_is_exceeded(
    async_client, app_instance, valid_headers
) -> None:
    app_instance.state.rate_limiter = RejectingRateLimiter()
    app_instance.state.inference_service = UnusedInferenceService()

    response = await async_client.post(
        "/v1/infer",
        headers=valid_headers,
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "12"
    assert response.json() == {
        "detail": {"error": "rate limit exceeded", "retry_after_s": 12.4}
    }


@pytest.mark.asyncio
async def test_infer_returns_503_when_circuit_breaker_is_open(
    async_client, app_instance, valid_headers, stub_adapter
) -> None:
    circuit_breaker = CircuitBreaker()
    for _ in range(5):
        await circuit_breaker.record_failure()

    app_instance.state.rate_limiter = AllowingRateLimiter()
    app_instance.state.inference_service = InferenceService(
        stub_adapter,
        InMemoryCache(),
        circuit_breaker,
    )

    response = await async_client.post(
        "/v1/infer",
        headers=valid_headers,
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    body = response.json()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(int(body["detail"]["retry_after_s"]))
    assert body["detail"]["error"] == "service unavailable"
    assert body["detail"]["retry_after_s"] > 0
    assert stub_adapter.calls == 0
