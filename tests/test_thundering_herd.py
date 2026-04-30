import asyncio
from typing import Any

import pytest

from app.core.circuit_breaker import CircuitBreaker
from app.core.schemas import InferRequest
from app.services.inference_service import InferenceService


class InMemoryRedis:
    def __init__(self) -> None:
        self._locks: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self._locks:
            return False
        self._locks[key] = value
        return True

    async def delete(self, key: str) -> None:
        self._locks.pop(key, None)


class InMemoryCache:
    def __init__(self) -> None:
        self.redis = InMemoryRedis()
        self._values: dict[str, dict[str, Any]] = {}

    def make_key(self, model: str, input: str, config: dict[str, Any] | None) -> str:
        return repr((model, input, config))

    async def get(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return self._values.get(self.make_key(model, input, config))

    async def set(
        self,
        model: str,
        input: str,
        config: dict[str, Any] | None,
        value: dict[str, Any],
    ) -> None:
        self._values[self.make_key(model, input, config)] = value


class SlowAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def infer(self, model: str, input: str, config: dict[str, Any] | None) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(0.05)
        return {
            "output": f"[stub] {input}",
            "usage": {
                "prompt_tokens": len(input.split()),
                "completion_tokens": 10,
                "total_tokens": len(input.split()) + 10,
            },
        }


@pytest.mark.asyncio
async def test_request_collapsing_calls_adapter_once_for_identical_concurrent_requests() -> None:
    adapter = SlowAdapter()
    cache = InMemoryCache()
    service = InferenceService(adapter, cache, CircuitBreaker())
    request = InferRequest(model="gpt-4o-mini", input="same request", config={"temperature": 0.1})

    responses = await asyncio.gather(
        *(service.infer(f"req-{index}", request) for index in range(10))
    )

    assert adapter.calls == 1
    assert sum(response.cache_hit for response in responses) == 9
    assert sum(not response.cache_hit for response in responses) == 1
    assert all(response.output == "[stub] same request" for response in responses)
