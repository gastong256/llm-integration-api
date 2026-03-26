import asyncio
import time
from typing import Any


class RequestCollapser:
    def __init__(
        self,
        redis_client: Any,
        lock_ttl_s: int = 10,
        wait_timeout_s: float = 15.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._redis = redis_client
        self._lock_ttl_s = lock_ttl_s
        self._wait_timeout_s = wait_timeout_s
        self._poll_interval_s = poll_interval_s

    def _lock_key(self, cache_key: str) -> str:
        return f"lock:{cache_key}"

    async def try_acquire(self, cache_key: str) -> bool:
        acquired = await self._redis.set(
            self._lock_key(cache_key),
            "1",
            ex=self._lock_ttl_s,
            nx=True,
        )
        return bool(acquired)

    async def wait_for_value(
        self,
        cache: Any,
        model: str,
        input: str,
        config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        deadline = time.perf_counter() + self._wait_timeout_s
        while time.perf_counter() < deadline:
            value = await cache.get(model, input, config)
            if value is not None:
                return value
            await asyncio.sleep(self._poll_interval_s)
        return None

    async def release(self, cache_key: str) -> None:
        await self._redis.delete(self._lock_key(cache_key))
