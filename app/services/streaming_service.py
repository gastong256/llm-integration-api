import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import Request
from opentelemetry import trace
from opentelemetry.trace import Span

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import UpstreamProviderError
from app.core.schemas import InferRequest

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)


def _set_trace_context(span: Span, model: str, request_id: str) -> None:
    span.set_attribute("llm.model", model)
    if request_id:
        span.set_attribute("request.id", request_id)


async def _audit_log(request_id: str, req: InferRequest, tokens: list[str]) -> None:
    logger.info(
        "stream_audit",
        request_id=request_id,
        model=req.model,
        token_count=len(tokens),
        output="".join(tokens),
    )


class StreamingService:
    def __init__(self, adapter: BaseLLMAdapter, circuit_breaker: CircuitBreaker) -> None:
        self._adapter = adapter
        self._cb = circuit_breaker

    async def stream(
        self, request_id: str, req: InferRequest, request: Request
    ) -> AsyncGenerator[str, None]:
        tokens: list[str] = []
        with tracer.start_as_current_span("stream_start") as span:
            _set_trace_context(span, req.model, request_id)

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
                async for chunk in self._adapter.stream(req.model, req.input, req.config):
                    if await request.is_disconnected():
                        span.add_event("stream_cancelled", {"reason": "client_disconnected"})
                        logger.info("client_disconnected", request_id=request_id)
                        break
                    span.add_event("stream_chunk", {"chunk.index": index})
                    data = json.dumps({"token": chunk, "index": index})
                    yield f"data: {data}\n\n"
                    tokens.append(chunk)
                    index += 1
                await self._cb.record_success()
                yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                span.add_event("stream_cancelled", {"reason": "cancelled_error"})
                logger.info("stream_cancelled", request_id=request_id)
                raise
            except TimeoutError:
                await self._cb.record_failure()
                yield 'data: {"error": "LLM provider timeout"}\n\n'
            except UpstreamProviderError:
                await self._cb.record_failure()
                yield 'data: {"error": "upstream provider error"}\n\n'
            except Exception:
                await self._cb.record_failure()
                raise
            finally:
                span.add_event("stream_audit_dispatch", {"token.count": len(tokens)})
                asyncio.create_task(_audit_log(request_id, req, tokens))
