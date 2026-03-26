import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.adapters.base import BaseLLMAdapter


class HttpLLMAdapter(BaseLLMAdapter):
    def __init__(self, base_url: str, timeout_s: int, api_key: str | None = None) -> None:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers=headers,
        )

    async def infer(self, model: str, input: str, config: dict[str, Any] | None) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/chat/completions",
            json=self._build_payload(model, input, config, stream=False),
        )
        response.raise_for_status()
        body = response.json()
        return {
            "output": body["choices"][0]["message"]["content"],
            "usage": body.get("usage", {}),
        }

    async def stream(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> AsyncGenerator[str, None]:
        async with self._client.stream(
            "POST",
            "/v1/chat/completions",
            json=self._build_payload(model, input, config, stream=True),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line.removeprefix("data: ")
                if data == "[DONE]":
                    break
                body = json.loads(data)
                content = body["choices"][0]["delta"].get("content")
                if content:
                    yield str(content)

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/v1/models")
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    async def aclose(self) -> None:
        await self._client.aclose()

    def _build_payload(
        self,
        model: str,
        input: str,
        config: dict[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": input}],
            "stream": stream,
        }
        if config:
            payload.update(config)
        return payload
