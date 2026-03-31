import time
from collections.abc import Callable
from typing import Any

import redis.exceptions
import structlog
from opentelemetry import trace
from opentelemetry.trace import Span

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import CircuitOpenError
from app.core.schemas import InferRequest, InferResponse
from app.infra.cache import SemanticCache
from app.infra.request_collapsing import RequestCollapser

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


def _set_trace_context(span: Span, model: str, request_id: str) -> None:
    span.set_attribute("llm.model", model)
    if request_id:
        span.set_attribute("request.id", request_id)


class InferenceService:
    def __init__(
        self,
        adapter: BaseLLMAdapter,
        cache: SemanticCache,
        circuit_breaker: CircuitBreaker,
        record_usage_metrics: Callable[[str, dict[str, int]], None] | None = None,
    ) -> None:
        self._adapter = adapter
        self._cache = cache
        self._cb = circuit_breaker
        self._record_usage_metrics = record_usage_metrics
        redis_client = getattr(cache, "_redis", None)
        self._collapser = RequestCollapser(redis_client) if redis_client is not None else None

    async def infer(self, request_id: str, req: InferRequest) -> InferResponse:
        t0 = time.perf_counter()
        with tracer.start_as_current_span("infer_flow") as flow_span:
            _set_trace_context(flow_span, req.model, request_id)

            with tracer.start_as_current_span("cache_check") as span:
                _set_trace_context(span, req.model, request_id)
                try:
                    cached = await self._cache.get(req.model, req.input, req.config)
                except redis.exceptions.ConnectionError as exc:
                    span.record_exception(exc)
                    logger.warning("redis_unavailable", operation="cache_get")
                    cached = None
                span.set_attribute("cache.hit", cached is not None)
            if cached is not None:
                latency_ms = (time.perf_counter() - t0) * 1000
                logger.info(
                    "infer_complete",
                    model=req.model,
                    latency_ms=latency_ms,
                    cache_hit=True,
                    status="success",
                )
                with tracer.start_as_current_span("response_build") as span:
                    _set_trace_context(span, req.model, request_id)
                    span.set_attribute("cache.hit", True)
                    response = InferResponse(
                        request_id=request_id,
                        output=cached["output"],
                        model=cached["model"],
                        usage=cached["usage"],
                        latency_ms=latency_ms,
                        cache_hit=True,
                    )
                return response

            with tracer.start_as_current_span("circuit_breaker_check") as span:
                _set_trace_context(span, req.model, request_id)
                is_open = await self._cb.is_open()
                span.set_attribute("circuit.state", "open" if is_open else "closed")
            if is_open:
                retry_after = int(self._cb.get_retry_after())
                logger.warning("circuit_open", retry_after_s=retry_after)
                raise CircuitOpenError(retry_after)

            cache_key = ""
            has_lock = False
            if self._collapser is not None:
                cache_key = self._cache._make_key(req.model, req.input, req.config)
                try:
                    has_lock = await self._collapser.try_acquire(cache_key)
                except redis.exceptions.ConnectionError:
                    logger.warning("redis_unavailable", operation="request_collapse_acquire")

                if not has_lock:
                    try:
                        collapsed = await self._collapser.wait_for_value(
                            self._cache,
                            req.model,
                            req.input,
                            req.config,
                        )
                    except redis.exceptions.ConnectionError:
                        logger.warning("redis_unavailable", operation="request_collapse_wait")
                        collapsed = None
                    if collapsed is not None:
                        latency_ms = (time.perf_counter() - t0) * 1000
                        logger.info(
                            "infer_complete",
                            model=req.model,
                            latency_ms=latency_ms,
                            cache_hit=True,
                            status="success",
                        )
                        with tracer.start_as_current_span("response_build") as span:
                            _set_trace_context(span, req.model, request_id)
                            span.set_attribute("cache.hit", True)
                            response = InferResponse(
                                request_id=request_id,
                                output=collapsed["output"],
                                model=collapsed["model"],
                                usage=collapsed["usage"],
                                latency_ms=latency_ms,
                                cache_hit=True,
                            )
                        return response

            try:
                with tracer.start_as_current_span("llm_call") as span:
                    _set_trace_context(span, req.model, request_id)
                    result: dict[str, Any] = await self._adapter.infer(
                        req.model, req.input, req.config
                    )
            except TimeoutError:
                await self._cb.record_failure()
                logger.warning(
                    "infer_complete",
                    model=req.model,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    cache_hit=False,
                    status="timeout",
                )
                raise
            except Exception:
                await self._cb.record_failure()
                logger.warning(
                    "infer_complete",
                    model=req.model,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    cache_hit=False,
                    status="error",
                )
                raise

            await self._cb.record_success()

            raw_usage = result.get("usage", {})
            usage = {
                "tokens_in": raw_usage.get("prompt_tokens", 0),
                "tokens_out": raw_usage.get("completion_tokens", 0),
            }
            if self._record_usage_metrics is not None:
                self._record_usage_metrics(req.model, usage)
            payload = {"output": result["output"], "model": req.model, "usage": usage}
            try:
                with tracer.start_as_current_span("cache_set") as span:
                    _set_trace_context(span, req.model, request_id)
                    span.set_attribute("cache.hit", False)
                    await self._cache.set(req.model, req.input, req.config, payload)
            except redis.exceptions.ConnectionError:
                logger.warning("redis_unavailable", operation="cache_set")
            finally:
                if has_lock and self._collapser is not None:
                    try:
                        await self._collapser.release(cache_key)
                    except redis.exceptions.ConnectionError:
                        logger.warning("redis_unavailable", operation="request_collapse_release")

            latency_ms = (time.perf_counter() - t0) * 1000
            logger.info(
                "infer_complete",
                model=req.model,
                latency_ms=latency_ms,
                cache_hit=False,
                status="success",
            )
            with tracer.start_as_current_span("response_build") as span:
                _set_trace_context(span, req.model, request_id)
                span.set_attribute("cache.hit", False)
                response = InferResponse(
                    request_id=request_id,
                    output=result["output"],
                    model=req.model,
                    usage=usage,
                    latency_ms=latency_ms,
                    cache_hit=False,
                )
            return response
