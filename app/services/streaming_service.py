import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import Request

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import UpstreamProviderError
from app.core.observability import (
    get_trace_correlation,
    log_safe_config,
    log_safe_input,
    log_safe_output,
)
from app.core.schemas import InferRequest
from app.observability.tracing import add_span_event, bind_span_context, get_tracer

logger = structlog.get_logger()
tracer = get_tracer(__name__)


async def _audit_log(
    request_id: str,
    req: InferRequest,
    tokens: list[str],
    trace_correlation: dict[str, str],
    status: str,
) -> None:
    logger.info(
        "stream_audit",
        request_id=request_id,
        model=req.model,
        token_count=len(tokens),
        status=status,
        input=log_safe_input(req.input),
        output=log_safe_output("".join(tokens)),
        config=log_safe_config(req.config),
        **trace_correlation,
    )


class StreamingService:
    def __init__(self, adapter: BaseLLMAdapter, circuit_breaker: CircuitBreaker) -> None:
        self._adapter = adapter
        self._cb = circuit_breaker

    async def stream(
        self, request_id: str, req: InferRequest, request: Request
    ) -> AsyncGenerator[str, None]:
        tokens: list[str] = []
        stream_status = "cancelled"
        with tracer.start_as_current_span("stream_start") as span:
            bind_span_context(span, request_id, {"llm.model": req.model})
            trace_correlation = get_trace_correlation()

            if await self._cb.is_open():
                retry_after = int(self._cb.get_retry_after())
                span.set_attribute("circuit.state", "open")
                logger.warning("circuit_open", retry_after_s=retry_after)
                payload: dict[str, Any] = {
                    "error": "service unavailable",
                    "retry_after_s": retry_after,
                }
                yield f"data: {json.dumps(payload)}\n\n"
                return

            span.set_attribute("circuit.state", "closed")

            try:
                index = 0
                client_disconnected = False
                async for chunk in self._adapter.stream(req.model, req.input, req.config):
                    if await request.is_disconnected():
                        add_span_event(span, "stream_cancelled", {"reason": "client_disconnected"})
                        logger.info("client_disconnected", request_id=request_id)
                        client_disconnected = True
                        break
                    add_span_event(span, "stream_chunk", {"chunk.index": index})
                    data = json.dumps({"token": chunk, "index": index})
                    yield f"data: {data}\n\n"
                    tokens.append(chunk)
                    index += 1
                if not client_disconnected:
                    await self._cb.record_success()
                    stream_status = "success"
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                stream_status = "cancelled"
                add_span_event(span, "stream_cancelled", {"reason": "cancelled_error"})
                logger.info("stream_cancelled", request_id=request_id)
                raise
            except TimeoutError:
                stream_status = "timeout"
                await self._cb.record_failure()
                yield 'data: {"error": "LLM provider timeout"}\n\n'
            except UpstreamProviderError:
                stream_status = "upstream_error"
                await self._cb.record_failure()
                yield 'data: {"error": "upstream provider error"}\n\n'
            except Exception:
                stream_status = "error"
                await self._cb.record_failure()
                raise
            finally:
                add_span_event(
                    span,
                    "stream_audit_dispatch",
                    {"token.count": len(tokens), "stream.status": stream_status},
                )
                asyncio.create_task(
                    _audit_log(request_id, req, tokens, trace_correlation, stream_status)
                )
