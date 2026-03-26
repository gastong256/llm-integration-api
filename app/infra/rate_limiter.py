import time
import uuid

import redis.asyncio as aioredis


class SlidingWindowRateLimiter:
    def __init__(self, redis_client: aioredis.Redis, rpm: int) -> None:
        self._redis = redis_client
        self._limit = rpm
        self._window = 60

    async def check(self, client_id: str) -> tuple[bool, float]:
        now = time.time()
        key = f"rl:{client_id}"
        member = f"{now}:{uuid.uuid4().hex[:8]}"

        pipe = self._redis.pipeline()
        pipe.zadd(key, {member: now})
        pipe.zremrangebyscore(key, 0, now - self._window)
        pipe.zcard(key)
        pipe.zrange(key, 0, 0, withscores=True)
        pipe.expire(key, self._window)
        results = await pipe.execute()

        count: int = results[2]
        oldest: list = results[3]

        if count <= self._limit:
            return True, 0.0

        # Remove the rejected entry — declined requests must not consume quota.
        await self._redis.zrem(key, member)

        retry_after = float(self._window)
        if oldest:
            oldest_score: float = oldest[0][1]
            retry_after = max(0.0, oldest_score + self._window - now)

        return False, retry_after
