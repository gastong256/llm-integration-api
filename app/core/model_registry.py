from pathlib import Path
from typing import Any

from app.core.exceptions import ModelVersionNotFound
from app.models import BaseSentimentWrapper, SentimentV1, SentimentV2
from sdk.models import BaseModelWrapper, WrapperRegistry

LOCAL_WRAPPERS: tuple[type[BaseSentimentWrapper], ...] = (SentimentV1, SentimentV2)


class ModelRegistry:
    def __init__(self, models_dir: Path) -> None:
        self._registry = WrapperRegistry()
        for wrapper_class in LOCAL_WRAPPERS:
            self._registry.register(wrapper_class(models_dir / f"{wrapper_class.version}.joblib"))

    @property
    def loaded_versions(self) -> list[str]:
        return self._registry.loaded_versions

    async def load_all(self) -> list[str]:
        return await self._registry.load_all()

    def get(self, version: str) -> BaseModelWrapper[Any, Any]:
        wrapper = self._registry.get(version)
        if wrapper is None:
            raise ModelVersionNotFound(version)
        return wrapper
