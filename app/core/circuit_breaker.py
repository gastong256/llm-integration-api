import asyncio
import time
from enum import Enum


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        window: float = 30.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._window = window
        self._state = State.CLOSED
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        return self._state

    async def is_open(self) -> bool:
        async with self._lock:
            if self._state is State.OPEN:
                if self._opened_at is None:
                    return False
                if time.monotonic() - self._opened_at >= self._recovery_timeout:
                    self._state = State.HALF_OPEN
                    return False
                return True
            return False

    def get_retry_after(self) -> float:
        if self._opened_at is None:
            return 0.0
        return max(0.0, self._recovery_timeout - (time.monotonic() - self._opened_at))

    async def record_failure(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._failures.append(now)
            # drop failures outside the sliding window
            self._failures = [t for t in self._failures if now - t <= self._window]
            if len(self._failures) >= self._failure_threshold:
                self._state = State.OPEN
                self._opened_at = now

    async def record_success(self) -> None:
        async with self._lock:
            if self._state is State.HALF_OPEN:
                self._state = State.CLOSED
                self._failures = []
                self._opened_at = None
