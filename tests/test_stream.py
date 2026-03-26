from collections.abc import AsyncGenerator
from typing import Any

import pytest

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.services.streaming_service import StreamingService


class AllowingRateLimiter:
    async def check(self, client_id: str) -> tuple[bool, float]:
        return True, 0.0


class StreamAdapter(BaseLLMAdapter):
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def infer(self, model: str, input: str, config: dict[str, Any] | None) -> dict[str, Any]:
        raise AssertionError("infer should not be called during stream tests")

    async def stream(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> AsyncGenerator[str, None]:
        for token in self._tokens:
            yield token

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_stream_returns_sse_events_and_done(
    async_client, app_instance, valid_headers
) -> None:
    app_instance.state.rate_limiter = AllowingRateLimiter()
    app_instance.state.streaming_service = StreamingService(
        StreamAdapter(["alpha", "beta"]),
        CircuitBreaker(),
    )

    async with async_client.stream(
        "POST",
        "/v1/infer/stream",
        headers=valid_headers,
        json={"model": "gpt-4o-mini", "input": "stream this"},
    ) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"token": "alpha", "index": 0}\n\n' in body
    assert 'data: {"token": "beta", "index": 1}\n\n' in body
    assert body.endswith("data: [DONE]\n\n")


@pytest.mark.asyncio
async def test_stream_returns_circuit_open_error_event(
    async_client, app_instance, valid_headers
) -> None:
    circuit_breaker = CircuitBreaker()
    for _ in range(5):
        await circuit_breaker.record_failure()

    app_instance.state.rate_limiter = AllowingRateLimiter()
    app_instance.state.streaming_service = StreamingService(
        StreamAdapter(["unused"]),
        circuit_breaker,
    )

    async with async_client.stream(
        "POST",
        "/v1/infer/stream",
        headers=valid_headers,
        json={"model": "gpt-4o-mini", "input": "stream this"},
    ) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert response.status_code == 200
    assert '"error": "service unavailable"' in body
    assert '"retry_after_s":' in body
    assert "[DONE]" not in body
