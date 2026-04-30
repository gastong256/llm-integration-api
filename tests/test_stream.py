import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import UpstreamProviderError
from app.services.streaming_service import StreamingService


class AllowingRateLimiter:
    limit = 60

    async def check(self, client_id: str) -> tuple[bool, float, int, int]:
        return True, 0.0, 59, 60


class RecordingCircuitBreaker:
    def __init__(self) -> None:
        self.success_calls = 0
        self.failure_calls = 0

    async def is_open(self) -> bool:
        return False

    async def record_success(self) -> None:
        self.success_calls += 1

    async def record_failure(self) -> None:
        self.failure_calls += 1

    def get_retry_after(self) -> float:
        return 0.0


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


class FailingStreamAdapter(BaseLLMAdapter):
    async def infer(self, model: str, input: str, config: dict[str, Any] | None) -> dict[str, Any]:
        raise AssertionError("infer should not be called during stream tests")

    async def stream(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> AsyncGenerator[str, None]:
        raise UpstreamProviderError(503)
        yield ""

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


@pytest.mark.asyncio
async def test_stream_returns_upstream_provider_error_event(
    async_client, app_instance, valid_headers
) -> None:
    app_instance.state.rate_limiter = AllowingRateLimiter()
    app_instance.state.streaming_service = StreamingService(
        FailingStreamAdapter(),
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
    assert '"error": "upstream provider error"' in body
    assert "[DONE]" not in body


@pytest.mark.asyncio
async def test_stream_audit_dispatch_carries_trace_correlation(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    tasks: list[asyncio.Task[None]] = []

    async def fake_audit_log(
        request_id: str,
        req,
        tokens: list[str],
        trace_correlation: dict[str, str],
        status: str,
    ) -> None:
        captured["request_id"] = request_id
        captured["tokens"] = tokens
        captured["trace_correlation"] = trace_correlation
        captured["input"] = req.input
        captured["status"] = status

    real_create_task = asyncio.create_task

    def fake_create_task(coro):
        task = real_create_task(coro)
        tasks.append(task)
        return task

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    monkeypatch.setattr("app.services.streaming_service._audit_log", fake_audit_log)
    monkeypatch.setattr("app.services.streaming_service.asyncio.create_task", fake_create_task)
    monkeypatch.setattr(
        "app.services.streaming_service.get_trace_correlation",
        lambda: {"trace_id": "a" * 32, "span_id": "b" * 16},
    )

    service = StreamingService(StreamAdapter(["alpha", "beta"]), CircuitBreaker())
    body = []
    async for chunk in service.stream(
        "req-123",
        type("Req", (), {"model": "gpt-4o-mini", "input": "stream this", "config": None})(),
        ConnectedRequest(),
    ):
        body.append(chunk)

    assert body[-1] == "data: [DONE]\n\n"
    await asyncio.gather(*tasks)
    assert captured["request_id"] == "req-123"
    assert captured["tokens"] == ["alpha", "beta"]
    assert captured["input"] == "stream this"
    assert captured["trace_correlation"] == {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
    }
    assert captured["status"] == "success"


@pytest.mark.asyncio
async def test_stream_disconnect_does_not_emit_done_or_record_success(monkeypatch) -> None:
    tasks: list[asyncio.Task[None]] = []
    captured: dict[str, Any] = {}

    async def fake_audit_log(
        request_id: str,
        req,
        tokens: list[str],
        trace_correlation: dict[str, str],
        status: str,
    ) -> None:
        captured["request_id"] = request_id
        captured["tokens"] = tokens
        captured["status"] = status

    real_create_task = asyncio.create_task

    def fake_create_task(coro):
        task = real_create_task(coro)
        tasks.append(task)
        return task

    class DisconnectingRequest:
        def __init__(self) -> None:
            self._calls = 0

        async def is_disconnected(self) -> bool:
            self._calls += 1
            return self._calls > 1

    monkeypatch.setattr("app.services.streaming_service._audit_log", fake_audit_log)
    monkeypatch.setattr("app.services.streaming_service.asyncio.create_task", fake_create_task)

    breaker = RecordingCircuitBreaker()
    service = StreamingService(StreamAdapter(["alpha", "beta"]), breaker)
    body: list[str] = []

    async for chunk in service.stream(
        "req-456",
        type("Req", (), {"model": "gpt-4o-mini", "input": "stream this", "config": None})(),
        DisconnectingRequest(),
    ):
        body.append(chunk)

    await asyncio.gather(*tasks)
    assert body == ['data: {"token": "alpha", "index": 0}\n\n']
    assert breaker.success_calls == 0
    assert breaker.failure_calls == 0
    assert captured["request_id"] == "req-456"
    assert captured["tokens"] == ["alpha"]
    assert captured["status"] == "cancelled"
