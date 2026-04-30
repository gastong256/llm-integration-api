from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Tracer


def get_tracer(name: str) -> Tracer:
    return trace.get_tracer(name)


def bind_span_context(
    span: Span,
    request_id: str = "",
    attributes: dict[str, Any] | None = None,
) -> None:
    if request_id:
        span.set_attribute("request.id", request_id)
    if attributes is None:
        return
    for key, value in attributes.items():
        span.set_attribute(key, value)


def add_span_event(
    span: Span,
    name: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    span.add_event(name, attributes or {})
