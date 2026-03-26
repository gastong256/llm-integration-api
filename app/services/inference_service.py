import time
from typing import Any

import structlog

from app.adapters.base import BaseLLMAdapter
from app.core.circuit_breaker import CircuitBreaker
from app.core.exceptions import CircuitOpenError
from app.core.schemas import InferRequest, InferResponse
from app.infra.cache import SemanticCache

logger = structlog.get_logger()


class InferenceService:
    def __init__(
        self,
        adapter: BaseLLMAdapter,
        cache: SemanticCache,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        self._adapter = adapter
        self._cache = cache
        self._cb = circuit_breaker

    async def infer(self, request_id: str, req: InferRequest) -> InferResponse:
        t0 = time.perf_counter()

        cached = await self._cache.get(req.model, req.input, req.config)
        if cached is not None:
            logger.info("cache_hit", model=req.model)
            return InferResponse(
                request_id=request_id,
                output=cached["output"],
                model=cached["model"],
                usage=cached["usage"],
                latency_ms=(time.perf_counter() - t0) * 1000,
                cache_hit=True,
            )

        if await self._cb.is_open():
            retry_after = int(self._cb.get_retry_after())
            logger.warning("circuit_open", retry_after_s=retry_after)
            raise CircuitOpenError(retry_after)

        try:
            result: dict[str, Any] = await self._adapter.infer(req.model, req.input, req.config)
            await self._cb.record_success()
        except Exception:
            await self._cb.record_failure()
            raise

        payload = {"output": result["output"], "model": req.model, "usage": result["usage"]}
        await self._cache.set(req.model, req.input, req.config, payload)

        # TODO: emit cache miss counter for hit-rate monitoring
        return InferResponse(
            request_id=request_id,
            output=result["output"],
            model=req.model,
            usage=result["usage"],
            latency_ms=(time.perf_counter() - t0) * 1000,
            cache_hit=False,
        )
