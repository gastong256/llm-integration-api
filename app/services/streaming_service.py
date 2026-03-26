import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import Request

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.core.schemas import InferRequest

logger = structlog.get_logger()


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

        if await self._cb.is_open():
            retry_after = int(self._cb.get_retry_after())
            logger.warning("circuit_open", retry_after_s=retry_after)
            payload: dict[str, Any] = {"error": "service unavailable", "retry_after_s": retry_after}
            yield f"data: {json.dumps(payload)}\n\n"
            return

        try:
            index = 0
            async for chunk in self._adapter.stream(req.model, req.input, req.config):
                if await request.is_disconnected():
                    logger.info("client_disconnected", request_id=request_id)
                    break
                data = json.dumps({"token": chunk, "index": index})
                yield f"data: {data}\n\n"
                tokens.append(chunk)
                index += 1
            await self._cb.record_success()
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            logger.info("stream_cancelled", request_id=request_id)
            raise
        except TimeoutError:
            await self._cb.record_failure()
            yield 'data: {"error": "LLM provider timeout"}\n\n'
        except Exception:
            await self._cb.record_failure()
            raise
        finally:
            asyncio.create_task(_audit_log(request_id, req, tokens))
