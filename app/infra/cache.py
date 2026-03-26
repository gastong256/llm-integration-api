import hashlib
import json
from typing import Any

import redis.asyncio as aioredis


class SemanticCache:
    def __init__(self, redis_client: aioredis.Redis, ttl: int) -> None:
        self._redis = redis_client
        self._ttl = ttl

    def _make_key(self, model: str, input: str, config: dict[str, Any] | None) -> str:
        payload = json.dumps({"model": model, "input": input, "config": config}, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return f"cache:{digest}"

    async def get(self, model: str, input: str, config: dict[str, Any] | None) -> dict | None:
        value = await self._redis.get(self._make_key(model, input, config))
        return json.loads(value) if value is not None else None

    async def set(self, model: str, input: str, config: dict[str, Any] | None, value: dict) -> None:
        await self._redis.setex(self._make_key(model, input, config), self._ttl, json.dumps(value))
