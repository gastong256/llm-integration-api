import asyncio
import random
from collections.abc import AsyncGenerator
from typing import Any

from app.adapters.base import BaseLLMAdapter


class StubLLMAdapter(BaseLLMAdapter):
    def __init__(self, failure_rate: float = 0.0, latency_ms: float = 150) -> None:
        self._failure_rate = failure_rate
        self._latency_ms = latency_ms

    async def infer(self, model: str, input: str, config: dict[str, Any] | None) -> dict[str, Any]:
        if random.random() < self._failure_rate:
            raise TimeoutError("stub timeout")
        await asyncio.sleep(self._latency_ms / 1000)
        return {
            "output": f"[stub] {input}",
            "usage": self._build_usage(input),
        }

    async def stream(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> AsyncGenerator[str, None]:
        if random.random() < self._failure_rate:
            raise TimeoutError("stub timeout")
        for i in range(10):
            await asyncio.sleep(0.1)
            yield f"token_{i}"

    async def health_check(self) -> bool:
        return self._failure_rate < 1.0

    def _build_usage(self, input: str) -> dict[str, int]:
        prompt_tokens = len(input.split())
        completion_tokens = 10
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
