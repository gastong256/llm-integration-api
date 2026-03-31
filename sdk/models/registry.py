import asyncio
from typing import Any

from sdk.models.base_wrapper import BaseModelWrapper


class WrapperRegistry:
    def __init__(self) -> None:
        self._wrappers: dict[str, BaseModelWrapper[Any, Any]] = {}
        self._loaded_versions: list[str] = []

    @property
    def loaded_versions(self) -> list[str]:
        return list(self._loaded_versions)

    def register(self, wrapper: BaseModelWrapper[Any, Any]) -> None:
        if wrapper.version in self._wrappers:
            raise ValueError(f"wrapper version already registered: {wrapper.version}")
        self._wrappers[wrapper.version] = wrapper

    async def load_all(self) -> list[str]:
        await asyncio.gather(*(wrapper.load() for wrapper in self._wrappers.values()))
        self._loaded_versions = list(self._wrappers.keys())
        return self.loaded_versions

    def get(self, version: str) -> BaseModelWrapper[Any, Any] | None:
        return self._wrappers.get(version)
