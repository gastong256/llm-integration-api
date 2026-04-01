import time

import structlog

from app.core.model_registry import ModelRegistry
from app.core.schemas import ClassifyResponse

logger = structlog.get_logger()


class ClassifyService:
    def __init__(self, model_registry: ModelRegistry) -> None:
        self._model_registry = model_registry

    async def classify(self, text: str, model_version: str) -> ClassifyResponse:
        t0 = time.perf_counter()
        wrapper = self._model_registry.get(model_version)
        payload = wrapper.input_schema.model_validate({"input": text})
        result = await wrapper.predict(payload)
        response = ClassifyResponse(
            label=result.label,
            confidence=result.confidence,
            model_version=model_version,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        logger.info(
            "classify_complete",
            model_version=model_version,
            label=response.label,
            latency_ms=response.latency_ms,
            status="success",
        )
        return response
