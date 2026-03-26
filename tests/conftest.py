from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.adapters.base import BaseLLMAdapter
from app.main import app


class StubAdapter(BaseLLMAdapter):
    def __init__(self) -> None:
        self.calls = 0

    async def infer(self, model: str, input: str, config: dict[str, Any] | None) -> dict[str, Any]:
        self.calls += 1
        return {
            "output": f"[stub] {input}",
            "usage": {
                "prompt_tokens": len(input.split()),
                "completion_tokens": 10,
                "total_tokens": len(input.split()) + 10,
            },
        }

    async def stream(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> AsyncGenerator[str, None]:
        if False:
            yield ""

    async def health_check(self) -> bool:
        return True


@pytest_asyncio.fixture
async def app_instance() -> AsyncGenerator:
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def async_client(app_instance) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app_instance),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest_asyncio.fixture
async def stub_adapter() -> StubAdapter:
    return StubAdapter()


@pytest_asyncio.fixture
async def valid_headers() -> dict[str, str]:
    return {"X-API-Key": "test-key-1"}
