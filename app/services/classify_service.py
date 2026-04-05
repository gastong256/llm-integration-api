import time

import structlog

from app.core.model_registry import ModelRegistry
from app.core.observability import get_trace_correlation, log_safe_input
from app.core.schemas import ClassifyResponse
from app.observability.tracing import bind_span_context, get_tracer

logger = structlog.get_logger()
tracer = get_tracer(__name__)


class ClassifyService:
    def __init__(self, model_registry: ModelRegistry) -> None:
        self._model_registry = model_registry

    async def classify(
        self,
        text: str,
        model_version: str,
        request_id: str = "",
    ) -> ClassifyResponse:
        t0 = time.perf_counter()
        safe_input = log_safe_input(text)
        with tracer.start_as_current_span("classify_flow") as span:
            bind_span_context(span, request_id, {"model.version": model_version})
            trace_correlation = get_trace_correlation()
            wrapper = self._model_registry.get(model_version)
            result = await wrapper.predict(wrapper.input_schema.model_validate({"input": text}))
            response = ClassifyResponse(
                label=result.label,
                confidence=result.confidence,
                model_version=model_version,
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
            span.set_attribute("classify.label", response.label)
            logger.info(
                "classify_complete",
                model_version=model_version,
                label=response.label,
                latency_ms=response.latency_ms,
                input=safe_input,
                status="success",
                **trace_correlation,
            )
            return response
