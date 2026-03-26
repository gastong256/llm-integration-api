from typing import Any

from pydantic import BaseModel, Field


class InferRequest(BaseModel):
    model: str
    input: str
    config: dict[str, Any] | None = None


class InferResponse(BaseModel):
    request_id: str
    output: str
    model: str
    usage: dict[str, int]
    latency_ms: float = Field(ge=0.0)
    cache_hit: bool


class ClassifyRequest(BaseModel):
    input: str


class ClassifyResponse(BaseModel):
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str
    latency_ms: float = Field(ge=0.0)


class HealthResponse(BaseModel):
    status: str
    redis: str
    llm_circuit: str
    models_loaded: list[str]


class ErrorResponse(BaseModel):
    error: str
    retry_after_s: float | None = Field(default=None, ge=0.0)
