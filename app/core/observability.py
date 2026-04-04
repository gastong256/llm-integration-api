from collections.abc import MutableMapping
from typing import Any

from opentelemetry import trace


def add_trace_correlation(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    del logger, method_name

    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return event_dict

    event_dict["trace_id"] = f"{span_context.trace_id:032x}"
    event_dict["span_id"] = f"{span_context.span_id:016x}"
    return event_dict
