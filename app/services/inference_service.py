import time
from collections.abc import Callable
from typing import Any

import redis.exceptions
import structlog

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import CircuitOpenError
from app.core.observability import log_safe_config, log_safe_input, log_safe_output
from app.core.schemas import InferRequest, InferResponse
from app.infra.cache import SemanticCache
from app.infra.request_collapsing import RequestCollapser
from app.observability.tracing import bind_span_context, get_tracer

logger = structlog.get_logger()
tracer = get_tracer(__name__)


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
        redis_client = getattr(cache, "redis", None)
        self._collapser = RequestCollapser(redis_client) if redis_client is not None else None

    async def infer(self, request_id: str, req: InferRequest) -> InferResponse:
        t0 = time.perf_counter()
        safe_input = log_safe_input(req.input)
        safe_config = log_safe_config(req.config)
        with tracer.start_as_current_span("infer_flow") as flow_span:
            bind_span_context(flow_span, request_id, {"llm.model": req.model})

            with tracer.start_as_current_span("cache_check") as span:
                bind_span_context(span, request_id, {"llm.model": req.model})
                try:
                    cached = await self._cache.get(req.model, req.input, req.config)
                except redis.exceptions.ConnectionError as exc:
                    span.record_exception(exc)
                    logger.warning("redis_unavailable", operation="cache_get")
                    cached = None
                span.set_attribute("cache.hit", cached is not None)
            if cached is not None:
                latency_ms = (time.perf_counter() - t0) * 1000
                self._log_infer_complete(
                    req.model,
                    latency_ms=latency_ms,
                    cache_hit=True,
                    status="success",
                    safe_input=safe_input,
                    safe_config=safe_config,
                    output=cached["output"],
                )
                with tracer.start_as_current_span("response_build") as span:
                    bind_span_context(span, request_id, {"llm.model": req.model})
                    span.set_attribute("cache.hit", True)
                    response = self._build_response(
                        request_id,
                        cached["output"],
                        cached["model"],
                        cached["usage"],
                        latency_ms,
                        cache_hit=True,
                    )
                return response

            with tracer.start_as_current_span("circuit_breaker_check") as span:
                bind_span_context(span, request_id, {"llm.model": req.model})
                is_open = await self._cb.is_open()
                span.set_attribute("circuit.state", "open" if is_open else "closed")
            if is_open:
                retry_after = int(self._cb.get_retry_after())
                logger.warning("circuit_open", retry_after_s=retry_after)
                raise CircuitOpenError(retry_after)

            cache_key = ""
            has_lock = False
            if self._collapser is not None:
                cache_key = self._cache.make_key(req.model, req.input, req.config)
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
                        self._log_infer_complete(
                            req.model,
                            latency_ms=latency_ms,
                            cache_hit=True,
                            status="success",
                            safe_input=safe_input,
                            safe_config=safe_config,
                            output=collapsed["output"],
                        )
                        with tracer.start_as_current_span("response_build") as span:
                            bind_span_context(span, request_id, {"llm.model": req.model})
                            span.set_attribute("cache.hit", True)
                            response = self._build_response(
                                request_id,
                                collapsed["output"],
                                collapsed["model"],
                                collapsed["usage"],
                                latency_ms,
                                cache_hit=True,
                            )
                        return response

            try:
                with tracer.start_as_current_span("llm_call") as span:
                    bind_span_context(span, request_id, {"llm.model": req.model})
                    result: dict[str, Any] = await self._adapter.infer(
                        req.model, req.input, req.config
                    )
            except TimeoutError:
                await self._cb.record_failure()
                self._log_infer_complete(
                    req.model,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    cache_hit=False,
                    status="timeout",
                    safe_input=safe_input,
                    safe_config=safe_config,
                    level="warning",
                )
                raise
            except Exception:
                await self._cb.record_failure()
                self._log_infer_complete(
                    req.model,
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    cache_hit=False,
                    status="error",
                    safe_input=safe_input,
                    safe_config=safe_config,
                    level="warning",
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
                    bind_span_context(span, request_id, {"llm.model": req.model})
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
            self._log_infer_complete(
                req.model,
                latency_ms=latency_ms,
                cache_hit=False,
                status="success",
                safe_input=safe_input,
                safe_config=safe_config,
                output=result["output"],
            )
            with tracer.start_as_current_span("response_build") as span:
                bind_span_context(span, request_id, {"llm.model": req.model})
                span.set_attribute("cache.hit", False)
                response = self._build_response(
                    request_id,
                    result["output"],
                    req.model,
                    usage,
                    latency_ms,
                    cache_hit=False,
                )
            return response

    def _build_response(
        self,
        request_id: str,
        output: str,
        model: str,
        usage: dict[str, int],
        latency_ms: float,
        *,
        cache_hit: bool,
    ) -> InferResponse:
        return InferResponse(
            request_id=request_id,
            output=output,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
        )

    def _log_infer_complete(
        self,
        model: str,
        *,
        latency_ms: float,
        cache_hit: bool,
        status: str,
        safe_input: str,
        safe_config: dict[str, Any] | None,
        output: str | None = None,
        level: str = "info",
    ) -> None:
        log = getattr(logger, level)
        payload: dict[str, Any] = {
            "model": model,
            "latency_ms": latency_ms,
            "cache_hit": cache_hit,
            "status": status,
            "input": safe_input,
            "config": safe_config,
        }
        if output is not None:
            payload["output"] = log_safe_output(output)
        log("infer_complete", **payload)
