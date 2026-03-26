import time

from app.core.model_registry import ModelRegistry
from app.core.schemas import ClassifyResponse


class ClassifyService:
    def __init__(self, model_registry: ModelRegistry) -> None:
        self._model_registry = model_registry

    async def classify(self, text: str, model_version: str) -> ClassifyResponse:
        t0 = time.perf_counter()
        label, confidence = await self._model_registry.predict(model_version, text)
        return ClassifyResponse(
            label=label,
            confidence=confidence,
            model_version=model_version,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
