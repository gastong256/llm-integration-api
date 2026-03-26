from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any


class BaseLLMAdapter(ABC):
    @abstractmethod
    async def infer(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def stream(
        self, model: str, input: str, config: dict[str, Any] | None
    ) -> AsyncGenerator[str, None]: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
